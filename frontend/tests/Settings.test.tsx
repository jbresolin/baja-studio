/**
 * Tests for the generic Settings page (src/pages/Settings.tsx).
 *
 * `fetch` is replaced by a small fake server that answers the settings
 * endpoints, so we can check what the page draws and what it sends.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fieldErrorsBySection, Settings } from "../src/pages/Settings";

// What the server would publish for the heartbeat module's settings.
const HEARTBEAT_SCHEMA = {
  title: "HeartbeatSettings",
  properties: {
    enabled: { type: "boolean", title: "Enabled", description: "When off, the module is stopped." },
    interval_s: { type: "number", title: "Interval S", minimum: 0.1, maximum: 60 },
    password: { type: "string", title: "Password", secret: true },
    mode: { $ref: "#/$defs/Mode" },
  },
  $defs: { Mode: { enum: ["auto", "manual"], title: "Mode", type: "string" } },
};

interface FakeResponse {
  status: number;
  body: unknown;
}

/** Install a fake `fetch`; `respond` decides the answer for each request. */
function fakeServer(respond: (method: string, path: string, body: unknown) => FakeResponse) {
  const requests: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      requests.push({ method, path, body });
      const answer = respond(method, path, body);
      return new Response(JSON.stringify(answer.body), { status: answer.status });
    }),
  );
  return requests;
}

/** Answers for an unchanged heartbeat section at `version`. */
function standardAnswers(version: number) {
  return (method: string, path: string): FakeResponse | null => {
    if (path === "/api/settings") {
      return { status: 200, body: [{ section: "heartbeat", title: "Heartbeat (demo)" }] };
    }
    if (path === "/api/settings/heartbeat/schema") return { status: 200, body: HEARTBEAT_SCHEMA };
    if (path === "/api/settings/heartbeat" && method === "GET") {
      return {
        status: 200,
        body: {
          section: "heartbeat",
          version,
          updated_at: "2026-10-01T12:00:00Z",
          values: { enabled: true, interval_s: 1, password: "********", mode: "auto" },
        },
      };
    }
    return null;
  };
}

function showSettingsPage() {
  render(
    <MemoryRouter initialEntries={["/settings/heartbeat"]}>
      <Routes>
        <Route path="/settings/:section" element={<Settings />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Settings page", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("draws one input per field, of the right kind, from the schema", async () => {
    fakeServer((method, path) => standardAnswers(1)(method, path) ?? { status: 404, body: {} });

    showSettingsPage();

    // Each field from the schema appears with its title as the label.
    const enabled = await screen.findByLabelText("Enabled");
    expect(enabled).toHaveAttribute("type", "checkbox");
    expect(enabled).toBeChecked();
    expect(screen.getByLabelText("Interval S")).toHaveValue(1);
    // A field marked secret is a password box, so the value is never shown.
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    // An enum (reached through $ref) becomes a dropdown.
    expect(screen.getByLabelText("Mode")).toHaveValue("auto");
    expect(screen.getByText("When off, the module is stopped.")).toBeInTheDocument();
  });

  it("saves the edited values together with the version they were based on", async () => {
    const requests = fakeServer((method, path, body) => {
      if (method === "PUT") {
        const values = (body as { values: Record<string, unknown> }).values;
        return {
          status: 200,
          body: { section: "heartbeat", version: 2, updated_at: "now", values },
        };
      }
      return standardAnswers(1)(method, path) ?? { status: 404, body: {} };
    });
    const user = userEvent.setup();
    showSettingsPage();

    // Change the interval from 1 to 2.5 and press Save.
    const interval = await screen.findByLabelText("Interval S");
    await user.clear(interval);
    await user.type(interval, "2.5");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    const save = requests.find((request) => request.method === "PUT");
    expect(save?.body).toMatchObject({ version: 1, values: { interval_s: 2.5 } });
    expect(screen.getByText("version 2")).toBeInTheDocument();
  });

  it("explains a version conflict instead of overwriting someone else's change", async () => {
    // The server says: these settings are already at version 2 (someone else saved).
    fakeServer((method, path) => {
      if (method === "PUT") {
        return {
          status: 409,
          body: { detail: { message: "settings are at version 2", current_version: 2 } },
        };
      }
      return standardAnswers(1)(method, path) ?? { status: 404, body: {} };
    });
    const user = userEvent.setup();
    showSettingsPage();

    await user.click(await screen.findByLabelText("Enabled"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/Someone else changed these settings/)).toBeInTheDocument();
    expect(screen.getByText(/now version 2/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load latest" })).toBeInTheDocument();
  });

  it("shows the server's message next to a field it rejected", async () => {
    fakeServer((method, path) => {
      if (method === "PUT") {
        return {
          status: 422,
          body: {
            detail: [{ loc: ["interval_s"], msg: "Input should be greater than or equal to 0.1" }],
          },
        };
      }
      return standardAnswers(1)(method, path) ?? { status: 404, body: {} };
    });
    const user = userEvent.setup();
    showSettingsPage();

    const interval = await screen.findByLabelText("Interval S");
    await user.clear(interval);
    await user.type(interval, "0");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("Input should be greater than or equal to 0.1"),
    ).toBeInTheDocument();
  });

  it("discard puts back the saved values and disables Save again", async () => {
    fakeServer((method, path) => standardAnswers(1)(method, path) ?? { status: 404, body: {} });
    const user = userEvent.setup();
    showSettingsPage();

    const enabled = await screen.findByLabelText("Enabled");
    // Nothing changed yet, so there is nothing to save.
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await user.click(enabled);
    expect(enabled).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(enabled).toBeChecked());
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

// A section with a nested model, shaped like the gateway's `tls`.
const NESTED_SCHEMA = {
  title: "GatewaySettings",
  properties: {
    hostname: { type: "string", title: "Hostname", examples: ["live.example.net"] },
    tls: { $ref: "#/$defs/GatewayTls" },
  },
  $defs: {
    GatewayTls: {
      type: "object",
      title: "GatewayTls",
      properties: {
        mode: { $ref: "#/$defs/TlsMode", default: "off" },
        cloudflare_api_token: { type: "string", title: "Cloudflare Api Token", secret: true },
      },
    },
    TlsMode: { enum: ["dns01", "off"], title: "TlsMode", type: "string" },
  },
};

describe("Settings page, nested models", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("draws a nested model's fields and saves the whole model with one field changed", async () => {
    const requests = fakeServer((method, path, body) => {
      if (path === "/api/settings") {
        return { status: 200, body: [{ section: "heartbeat", title: "Gateway" }] };
      }
      if (path.endsWith("/schema")) return { status: 200, body: NESTED_SCHEMA };
      const values =
        method === "PUT"
          ? (body as { values: Record<string, unknown> }).values
          : {
              hostname: "live.example.net",
              tls: { mode: "off", cloudflare_api_token: "********" },
            };
      return {
        status: 200,
        body: { section: "heartbeat", version: 1, updated_at: "now", values },
      };
    });
    const user = userEvent.setup();
    showSettingsPage();

    // A schema example shows as the text box's placeholder (greyed out while it is empty).
    expect(await screen.findByLabelText("Hostname")).toHaveAttribute(
      "placeholder",
      "live.example.net",
    );

    // The nested fields are real inputs, not a JSON text box: a dropdown and a password.
    const mode = await screen.findByLabelText("TlsMode");
    expect(mode).toHaveValue("off");
    expect(screen.getByLabelText("Cloudflare Api Token")).toHaveAttribute("type", "password");

    await user.selectOptions(mode, "dns01");
    await user.click(screen.getByRole("button", { name: "Save" }));

    // The masked token goes back as the mask, which the server reads as "unchanged".
    await screen.findByText("Saved.");
    const save = requests.find((request) => request.method === "PUT");
    expect(save?.body).toMatchObject({
      values: { tls: { mode: "dns01", cloudflare_api_token: "********" } },
    });
  });
});

describe("Settings page, a module with its own settings page (WP5c)", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  const PLAYOUT_VALUES = { enabled: true, tools: { ffprobe_path: "", ffmpeg_path: "" } };

  function showPlayoutSection(onSave: (body: unknown) => FakeResponse) {
    const requests = fakeServer((method, path, body) => {
      if (path === "/api/settings") {
        return { status: 200, body: [{ section: "playout", title: "Playout" }] };
      }
      if (path === "/api/settings/playout" && method === "PUT") return onSave(body);
      if (path === "/api/settings/playout") {
        return {
          status: 200,
          body: { section: "playout", version: 4, updated_at: "", values: PLAYOUT_VALUES },
        };
      }
      return { status: 404, body: {} };
    });
    render(
      <MemoryRouter initialEntries={["/settings/playout"]}>
        <Routes>
          <Route path="/settings/:section" element={<Settings />} />
        </Routes>
      </MemoryRouter>,
    );
    return requests;
  }

  it("links to the module's page and shows its settings as JSON only, never as fields", async () => {
    const requests = showPlayoutSection(() => ({ status: 200, body: {} }));

    const json = await screen.findByLabelText("playout settings JSON");
    expect(JSON.parse((json as HTMLTextAreaElement).value)).toEqual(PLAYOUT_VALUES);
    expect(screen.getByRole("link", { name: "⚙ Playout setup" })).toHaveAttribute(
      "href",
      "/playout/settings",
    );
    // No form built from the schema: the fields live on the playout page alone.
    expect(requests.some((request) => request.path.endsWith("/schema"))).toBe(false);
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("saves edited JSON with the version it started from, and refuses text that is not JSON", async () => {
    const user = userEvent.setup();
    const saves: unknown[] = [];
    showPlayoutSection((body) => {
      saves.push(body);
      return {
        status: 200,
        body: { section: "playout", version: 5, updated_at: "", values: body },
      };
    });
    const json = await screen.findByLabelText("playout settings JSON");

    await user.clear(json);
    await user.type(json, "not json");
    await user.click(screen.getByRole("button", { name: "Save JSON" }));
    expect(screen.getByTestId("json-errors")).toHaveTextContent("Not valid JSON, nothing saved");
    expect(saves).toEqual([]);

    await user.clear(json);
    await user.paste('{"enabled": false}');
    await user.click(screen.getByRole("button", { name: "Save JSON" }));

    await waitFor(() => expect(saves).toEqual([{ version: 4, values: { enabled: false } }]));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("does the same for audio, whose fields live on Audio setup (WP9b)", async () => {
    vi.unstubAllGlobals();
    const requests = fakeServer((_method, path) => {
      if (path === "/api/settings") {
        return { status: 200, body: [{ section: "audio", title: "Audio" }] };
      }
      if (path === "/api/settings/audio") {
        return {
          status: 200,
          body: { section: "audio", version: 2, updated_at: "", values: { enabled: true } },
        };
      }
      return { status: 404, body: {} };
    });
    render(
      <MemoryRouter initialEntries={["/settings/audio"]}>
        <Routes>
          <Route path="/settings/:section" element={<Settings />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByLabelText("audio settings JSON");
    expect(screen.getByRole("link", { name: "⚙ Audio setup" })).toHaveAttribute(
      "href",
      "/audio/config",
    );
    expect(requests.some((request) => request.path.endsWith("/schema"))).toBe(false);
  });
});

describe("fieldErrorsBySection", () => {
  it("shows a problem deep inside a section under that section, with the rest of its path", () => {
    // Regression, bench 2026-11-08: the error for sources.local_folders.0 was
    // keyed by its full path, which no field on the page has, so nothing showed.
    expect(
      fieldErrorsBySection([
        { field: "sources.local_folders.0", message: "must be an object" },
        { field: "sources.orchestrator.base_url", message: "bad URL" },
        { field: "interval_s", message: "too small" },
      ]),
    ).toEqual({
      sources: "local_folders.0: must be an object; orchestrator.base_url: bad URL",
      interval_s: "too small",
    });
  });
});

describe("Settings page, the cameras section (WP10)", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  const CAMERAS_VALUES = {
    enabled: true,
    cameras: [{ id: "smtav", label: "SMTAV", driver: "visca_ip" }],
    controllers: [],
    motion: { joystick_deadband: 0.1 },
  };

  it("shows only the JSON, with a link to Camera setup, and never the camera fields", async () => {
    const requests = fakeServer((_method, path) => {
      if (path === "/api/settings") {
        return { status: 200, body: [{ section: "cameras", title: "Cameras" }] };
      }
      if (path === "/api/settings/cameras") {
        return {
          status: 200,
          body: { section: "cameras", version: 9, updated_at: "", values: CAMERAS_VALUES },
        };
      }
      return { status: 404, body: {} };
    });
    render(
      <MemoryRouter initialEntries={["/settings/cameras"]}>
        <Routes>
          <Route path="/settings/:section" element={<Settings />} />
        </Routes>
      </MemoryRouter>,
    );

    // The whole section, as the JSON editor shows it...
    const json = await screen.findByLabelText("cameras settings JSON");
    expect(JSON.parse((json as HTMLTextAreaElement).value)).toEqual(CAMERAS_VALUES);
    // ...and the one place where the fields are edited.
    expect(screen.getByRole("link", { name: "⚙ Camera setup" })).toHaveAttribute(
      "href",
      "/cameras/config",
    );
    // No schema-built form: the same field is never offered on two pages.
    expect(requests.some((request) => request.path.endsWith("/schema"))).toBe(false);
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
