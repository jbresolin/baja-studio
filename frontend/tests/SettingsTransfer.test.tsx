/**
 * Tests for Settings → Export / import (src/pages/SettingsTransfer.tsx).
 *
 * The upload goes over HTTP (a fake `fetch`); the dry run and the apply are
 * commands over the state socket (a fake server socket the test answers).
 */

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DryRun, SectionPlan } from "../src/lib/settingsTransfer";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { SettingsTransfer } from "../src/pages/SettingsTransfer";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

interface SentCommand {
  request_id: string;
  module: string;
  command: string;
  args: Record<string, unknown>;
}

function section(extra: Partial<SectionPlan>): SectionPlan {
  return {
    section: "playout",
    status: "changes",
    messages: [],
    file_settings_version: 1,
    current_settings_version: 1,
    added: [],
    removed: [],
    changed: [],
    secrets_to_enter: [],
    ...extra,
  };
}

function dryRun(extra: Partial<DryRun> = {}): DryRun {
  return {
    token: "token-1",
    app_version: "1.0.0",
    exported_at: "2026-11-07T15:00:00+00:00",
    hostname: "STREAM-PC",
    needs_partial: false,
    has_changes: true,
    sections: [
      section({
        section: "playout",
        added: [{ key: "obs.host", old: null, new: "10.0.0.2", secret: false }],
        changed: [{ key: "obs.password", old: "<secret>", new: "<secret>", secret: true }],
        removed: [
          { key: "sources.local_folders[0].path", old: "D:\\old", new: null, secret: false },
        ],
        secrets_to_enter: ["playout.obs.password"],
      }),
      section({ section: "gateway", status: "unchanged" }),
    ],
    ...extra,
  };
}

let uploads: Blob[];

beforeEach(() => {
  uploads = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path: string, init?: RequestInit) => {
      uploads.push(init?.body as Blob);
      return new Response(JSON.stringify({ upload_id: "up-1" }), { status: 200 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function showTransfer(): FakeSocket {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  sockets.latest().open();
  sockets.latest().receive({ type: "snapshot", seq: 1, state: {} });
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <SettingsTransfer />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  return sockets.latest();
}

/** Wait for the page to send a command, then answer it. */
async function answer(server: FakeSocket, result: unknown, ok = true): Promise<SentCommand> {
  await vi.waitFor(() => expect(server.sent.length).toBeGreaterThan(0));
  const sent = server.sent.at(-1) as SentCommand;
  await act(async () =>
    server.receive(
      ok
        ? { type: "reply", request_id: sent.request_id, ok: true, result }
        : { type: "reply", request_id: sent.request_id, ok: false, error: result },
    ),
  );
  return sent;
}

/** Wait until the page sends `command`, then answer it (`ok` false: refuse it with `result`). */
async function answerNamed(
  server: FakeSocket,
  command: string,
  result: unknown,
  ok = true,
): Promise<SentCommand> {
  let sent: SentCommand | undefined;
  await vi.waitFor(() => {
    sent = (server.sent as SentCommand[]).find((message) => message.command === command);
    expect(sent).toBeDefined();
  });
  await act(async () =>
    server.receive(
      ok
        ? { type: "reply", request_id: sent!.request_id, ok: true, result }
        : { type: "reply", request_id: sent!.request_id, ok: false, error: result },
    ),
  );
  return sent!;
}

async function chooseFile(extra: Record<string, unknown> = {}): Promise<void> {
  const content = JSON.stringify({ format: "baja-studio-settings", ...extra });
  const file = new File([content], "settings.json", { type: "application/json" });
  await userEvent.upload(screen.getByLabelText(/choose one/), file);
}

const ROUTING = { links: { "1": ["bus1", "bus2"] }, offsets: { "9:bus1": 2 } };

describe("Settings export", () => {
  it("asks the home module for the export, adds audio's routing and saves it as a file", async () => {
    const blobs: Blob[] = [];
    const names: string[] = [];
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn((blob: Blob) => {
        blobs.push(blob);
        return "blob:settings";
      }),
      revokeObjectURL: vi.fn(),
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      names.push(this.download);
    });
    const server = showTransfer();

    await userEvent.click(screen.getByRole("button", { name: "Export settings" }));
    const sent = await answerNamed(server, "export_settings", {
      file_name: "BajaStudio-settings-STREAM-PC-20261107-150000.json",
      document: { format: "baja-studio-settings" },
    });
    // The link matrix and in-ear offsets are audio's tables, not settings (WP9b §4).
    const routing = await answerNamed(server, "export_routing", ROUTING);
    await vi.waitFor(() => expect(names).toHaveLength(1));

    expect([sent.module, sent.command]).toEqual(["home", "export_settings"]);
    expect([routing.module, routing.command]).toEqual(["audio", "export_routing"]);
    expect(names).toEqual(["BajaStudio-settings-STREAM-PC-20261107-150000.json"]);
    expect(JSON.parse(await blobs[0]!.text())).toEqual({
      format: "baja-studio-settings",
      audio_routing: ROUTING,
    });
    click.mockRestore();
  });

  it("saves the settings alone when audio is off", async () => {
    const blobs: Blob[] = [];
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn((blob: Blob) => {
        blobs.push(blob);
        return "blob:settings";
      }),
      revokeObjectURL: vi.fn(),
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const server = showTransfer();

    await userEvent.click(screen.getByRole("button", { name: "Export settings" }));
    await answerNamed(server, "export_settings", {
      file_name: "settings.json",
      document: { format: "baja-studio-settings" },
    });
    await answerNamed(server, "export_routing", "audio is not running", false);
    await vi.waitFor(() => expect(blobs).toHaveLength(1));

    expect(JSON.parse(await blobs[0]!.text())).toEqual({ format: "baja-studio-settings" });
    click.mockRestore();
  });
});

describe("Audio routing import (WP9b)", () => {
  it("previews what the file's link matrix and offsets change, then applies them", async () => {
    const server = showTransfer();
    await chooseFile({ audio_routing: ROUTING });
    await answerNamed(server, "import_settings_dryrun", dryRun());
    const preview = await answerNamed(server, "import_routing", {
      added: ["1:bus2"],
      removed: ["5:bus1"],
      offsets_changed: ["9:bus1"],
      applied: false,
    });

    expect(preview.args).toEqual({ ...ROUTING, dry_run: true });
    const routing = await screen.findByTestId("import-audio-routing");
    expect(routing).toHaveTextContent("audio routing (link matrix, in-ear offsets): changes (3)");
    expect(routing).toHaveTextContent("link 1:bus2");
    expect(routing).toHaveTextContent("unlink 5:bus1");
    expect(routing).toHaveTextContent("offset 9:bus1: 2 dB");

    server.sent = [];
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await answerNamed(server, "import_settings_apply", { results: [], secrets_to_enter: [] });
    const applied = await answerNamed(server, "import_routing", { applied: true });

    expect(applied.args).toEqual(ROUTING);
    expect(await screen.findByTestId("apply-results")).toHaveTextContent("audio routing: applied");
  });

  it("shows why audio refused the file's routing, and imports the settings anyway", async () => {
    const server = showTransfer();
    await chooseFile({ audio_routing: { links: "everything", offsets: {} } });
    await answerNamed(server, "import_settings_dryrun", dryRun());
    await answerNamed(server, "import_routing", "the audio routing's 'links' must be ...", false);

    const routing = await screen.findByTestId("import-audio-routing");
    expect(routing).toHaveTextContent("cannot import");
    expect(routing).toHaveTextContent("the audio routing's 'links' must be");
    expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled();
  });
});

describe("Settings import", () => {
  it("uploads the file, then shows the dry run per module", async () => {
    const server = showTransfer();

    await chooseFile();
    const sent = await answer(server, dryRun());

    expect(uploads).toHaveLength(1);
    expect([sent.command, sent.args]).toEqual(["import_settings_dryrun", { upload_id: "up-1" }]);
    expect(screen.getByTestId("dry-run")).toHaveTextContent("Exported from STREAM-PC");
    const playout = screen.getByTestId("import-playout");
    expect(playout).toHaveTextContent("playout: changes (3)");
    expect(playout).toHaveTextContent('obs.host: "10.0.0.2"');
    // A secret is flagged, and its value never shown.
    expect(playout).toHaveTextContent('obs.password: "<secret>" → "<secret>" (secret)');
    expect(playout).toHaveTextContent('sources.local_folders[0].path: "D:\\\\old"');
    expect(playout).toHaveTextContent("Enter again after importing: playout.obs.password");
    expect(screen.getByTestId("import-gateway")).toHaveTextContent("gateway: no change");
  });

  it("applies with the dry run's token and lists the secrets to enter again", async () => {
    const server = showTransfer();
    await chooseFile();
    await answer(server, dryRun());

    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    const sent = await answer(server, {
      results: [
        { section: "playout", result: "applied" },
        { section: "gateway", result: "unchanged" },
      ],
      secrets_to_enter: ["playout.obs.password"],
    });

    expect([sent.command, sent.args]).toEqual([
      "import_settings_apply",
      { upload_id: "up-1", token: "token-1", apply_partial: false },
    ]);
    const results = screen.getByTestId("apply-results");
    expect(results).toHaveTextContent("playout: applied");
    expect(within(results).getByTestId("secrets-to-enter")).toHaveTextContent(
      "Enter these again in their settings pages: playout.obs.password",
    );
  });

  it("keeps Apply off until 'apply partial' is ticked when a module cannot be imported", async () => {
    const server = showTransfer();
    await chooseFile();
    await answer(
      server,
      dryRun({
        needs_partial: true,
        sections: [
          section({
            section: "cameras",
            status: "invalid",
            messages: ["cameras[0].address.port: Input should be a valid integer (got 'abc')"],
          }),
          section({
            section: "playout",
            added: [{ key: "obs.host", old: null, new: "10.0.0.2", secret: false }],
          }),
        ],
      }),
    );

    // The module that cannot be imported is open, with the reason.
    expect(screen.getByTestId("import-cameras")).toHaveTextContent("cameras: cannot import");
    expect(screen.getByTestId("import-cameras")).toHaveTextContent(
      "Input should be a valid integer",
    );
    const apply = screen.getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();

    await userEvent.click(screen.getByLabelText(/Apply partial/));
    expect(apply).toBeEnabled();
    await userEvent.click(apply);
    const sent = server.sent.at(-1) as SentCommand;
    expect(sent.args.apply_partial).toBe(true);
  });

  it("shows why a file was refused", async () => {
    const server = showTransfer();
    await chooseFile();

    await answer(server, "this is not a Baja Studio settings file (use Export to make one)", false);

    expect(screen.getByRole("alert")).toHaveTextContent("this is not a Baja Studio settings file");
    expect(screen.queryByTestId("dry-run")).not.toBeInTheDocument();
  });

  it("has nothing to apply when the file changes nothing", async () => {
    const server = showTransfer();
    await chooseFile();

    await answer(
      server,
      dryRun({ has_changes: false, sections: [section({ status: "unchanged" })] }),
    );

    expect(screen.getByTestId("dry-run")).toHaveTextContent("Nothing to change.");
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  });
});
