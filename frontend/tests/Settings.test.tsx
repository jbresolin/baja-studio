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
    hostname: { type: "string", title: "Hostname" },
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
