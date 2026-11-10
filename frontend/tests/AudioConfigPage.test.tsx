/**
 * Tests for the audio setup page (src/pages/AudioConfigPage.tsx,
 * src/lib/useAudioSettings.ts and components/audio/config/).
 *
 * Settings come and go over HTTP (`fetch`, faked by a tiny in-memory server
 * that records every save); live state and commands over the WebSocket (a
 * fake socket the test plays). The headline test is the 2026 bug: edits
 * must survive the state broadcasts that keep arriving while one types.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AudioSettingsValues } from "../src/lib/audio";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { AudioConfigPage } from "../src/pages/AudioConfigPage";
import { audioSettings, audioState } from "./audioFixtures";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

let server: FakeSocket;
let saves: { version: number; values: AudioSettingsValues }[];
let stored: { section: string; version: number; updated_at: string; values: AudioSettingsValues };
let nextSaveAnswer: Response | null;

beforeEach(() => {
  saves = [];
  nextSaveAnswer = null;
  stored = { section: "audio", version: 4, updated_at: "", values: audioSettings() };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        saves.push(body);
        if (nextSaveAnswer) return nextSaveAnswer;
        stored = { ...stored, version: stored.version + 1, values: body.values };
      }
      return Response.json(stored);
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

interface SentCommand {
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

async function showSetup() {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <AudioConfigPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() =>
    server.receive({ type: "snapshot", seq: 1, client_id: "me", state: { audio: audioState() } }),
  );
  await screen.findByTestId("save-bar");
}

/** The server sends a state patch (mixer values, macro states...). */
function broadcast(path: string, value: unknown, seq: number) {
  act(() => server.receive({ type: "patch", module: "audio", path, value, seq }));
}

function lastCommand(): SentCommand {
  return server.sent.at(-1) as SentCommand;
}

function openSection(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
}

describe("Audio setup page", () => {
  it("keeps unsaved edits while state broadcasts keep arriving (the 2026 bug)", async () => {
    await showSetup();
    openSection("Channels");
    fireEvent.change(screen.getByLabelText("Ch 1 label"), { target: { value: "ANCHOR" } });

    // The mixer keeps pushing: levels, macro states, a channel renamed on the desk.
    broadcast("channels.1", { fader: 0.6, db: "-6.0 dB", muted: false, name: "Desk" }, 2);
    broadcast("macros", { "host1-talk-prod": { active: true } }, 3);
    broadcast("mixer.packets_per_s", { sent: 2, received: 9 }, 4);

    expect(screen.getByLabelText("Ch 1 label")).toHaveValue("ANCHOR");
    expect(screen.getByTestId("save-bar")).toHaveTextContent("Unsaved changes");
    expect(within(screen.getByTestId("channel-table")).getByText("Desk")).toBeInTheDocument();
  });

  it("saves explicitly, from the version it was loaded at", async () => {
    await showSetup();
    openSection("Channels");
    fireEvent.click(screen.getByLabelText("Bus 1 visible"));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.version).toBe(4);
    expect(saves[0]!.values.channels.bus1!.visible).toBe(true);
    await screen.findByText("Saved and applied.");
    expect(screen.getByTestId("save-bar")).toHaveTextContent("No unsaved changes");
  });

  it("picks the camera operator's bus by its label and saves it", async () => {
    await showSetup();
    openSection("Channels");
    const picker = screen.getByLabelText("Camera operator's bus (talkback)");
    expect(picker).toHaveValue("bus6");
    expect(within(picker).getByRole("option", { name: "IEM HOST 1 (Bus 1)" })).toBeInTheDocument();

    fireEvent.change(picker, { target: { value: "bus1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.values.talkback_bus).toBe("bus1");
  });

  it("discard goes back to what was saved", async () => {
    await showSetup();
    openSection("Channels");
    fireEvent.change(screen.getByLabelText("Ch 1 label"), { target: { value: "ANCHOR" } });

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.getByLabelText("Ch 1 label")).toHaveValue("HOST 1");
  });

  it("a stale save is a conflict: nothing is overwritten and the edits stay", async () => {
    await showSetup();
    openSection("Channels");
    fireEvent.change(screen.getByLabelText("Ch 1 label"), { target: { value: "ANCHOR" } });
    nextSaveAnswer = Response.json(
      { detail: { message: "stale", current_version: 5 } },
      { status: 409 },
    );

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/Someone else saved the audio setup \(now version 5\)/);
    expect(screen.getByLabelText("Ch 1 label")).toHaveValue("ANCHOR");
  });

  it("says when someone else saved, and loads it by itself when nothing is unsaved", async () => {
    await showSetup();
    openSection("Channels");
    fireEvent.change(screen.getByLabelText("Ch 1 label"), { target: { value: "ANCHOR" } });

    broadcast("settings_version", 5, 2);
    expect(
      await screen.findByText(/Someone else saved the audio setup \(version 5\)/),
    ).toBeVisible();
    expect(screen.getByLabelText("Ch 1 label")).toHaveValue("ANCHOR");

    // Discard: no unsaved edits now, so the newer version is loaded.
    stored = {
      ...stored,
      version: 5,
      values: {
        ...stored.values,
        channels: {
          ...stored.values.channels,
          "1": { label: "THEIRS", color: "#e74c3c", visible: true },
        },
      },
    };
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(screen.getByLabelText("Ch 1 label")).toHaveValue("THEIRS"));
  });

  it("midi learn highlights the control the operator touched", async () => {
    await showSetup();
    openSection("M-VAVE");

    fireEvent.click(screen.getByRole("button", { name: "Learn: touch a control" }));
    expect(lastCommand()).toMatchObject({ command: "midi_learn", args: { action: "arm" } });
    broadcast(
      "midi.learn",
      {
        armed: false,
        by: "web:me",
        captured: { control: "button:2_mute", at: "2026-11-05T10:00:00Z" },
      },
      2,
    );

    expect(screen.getByTestId("learned-control")).toHaveTextContent("button 2_mute");
    expect(screen.getByLabelText("strip 2 M")).toHaveFocus();
  });

  it("buttons can run a macro", async () => {
    await showSetup();
    openSection("M-VAVE");

    fireEvent.change(screen.getByLabelText("strip 3 □"), {
      target: { value: "macro:host1-talk-prod" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.values.midi.buttons["3_sel"]).toEqual({
      type: "macro",
      channel: null,
      bus: null,
      macro: "host1-talk-prod",
    });
  });

  it("a link cell applies at once with a command, not with Save", async () => {
    await showSetup();
    openSection("Link matrix");

    fireEvent.click(screen.getByRole("button", { name: "HOST 1 to IEM HOST 1: linked" }));

    expect(lastCommand()).toMatchObject({
      command: "set_link",
      args: { channel: "1", bus: "bus1", state: "paused" },
    });
    expect(screen.getByTestId("save-bar")).toHaveTextContent("No unsaved changes");
  });

  it("the macro editor adds and reorders steps and shows a dry run", async () => {
    await showSetup();
    openSection("Macros");
    const editor = screen.getByTestId("macro-editor");
    fireEvent.click(within(editor).getAllByRole("button", { name: "Edit" })[0]!);

    fireEvent.change(screen.getByLabelText("add a step to Turning on"), {
      target: { value: "set_mute" },
    });
    fireEvent.click(screen.getByLabelText("move Turning on step 2 up"));
    expect(screen.getByLabelText("Turning on step 1 kind")).toHaveValue("set_mute");

    fireEvent.click(screen.getByRole("button", { name: "Dry run" }));
    const ask = lastCommand();
    expect(ask).toMatchObject({ command: "dry_run" });
    expect((ask.args.steps as { op: string }[])[0]!.op).toBe("set_mute");
    act(() =>
      server.receive({
        type: "reply",
        request_id: ask.request_id,
        ok: true,
        result: { changes: [{ kind: "mute", text: "HOST 1: mute" }] },
      }),
    );
    // The "turning off" steps are asked about next.
    await waitFor(() => expect(lastCommand().request_id).not.toBe(ask.request_id));
    const off = lastCommand();
    act(() =>
      server.receive({
        type: "reply",
        request_id: off.request_id,
        ok: true,
        result: { changes: [] },
      }),
    );

    expect(await screen.findByTestId("dry-run")).toHaveTextContent("HOST 1: mute");
  });

  it("edits the LR-assign and link steps of talk to production and dry-runs them (WP9b)", async () => {
    await showSetup();
    openSection("Macros");
    const editor = screen.getByTestId("macro-editor");
    fireEvent.click(within(editor).getAllByRole("button", { name: "Edit" })[0]!);

    fireEvent.change(screen.getByLabelText("add a step to Turning on"), {
      target: { value: "set_lr_assign" },
    });
    fireEvent.change(screen.getByLabelText("add a step to Turning on"), {
      target: { value: "set_link" },
    });
    fireEvent.change(screen.getByLabelText("Turning on step 3 bus"), {
      target: { value: "bus3" },
    });
    expect(screen.getByLabelText("Turning on step 2 main mix")).toHaveValue("off");
    expect(screen.getByLabelText("Turning on step 3 link")).toHaveValue("paused");

    fireEvent.click(screen.getByRole("button", { name: "Dry run" }));

    const steps = lastCommand().args.steps as Record<string, unknown>[];
    expect(steps.slice(1)).toEqual([
      { op: "set_lr_assign", channel: "1", on: false },
      { op: "set_link", channel: "1", bus: "bus3", state: "paused" },
    ]);
  });

  it("the D200 card lists this host's URLs", async () => {
    await showSetup();
    openSection("D200");

    expect(screen.getByTestId("d200-card")).toHaveTextContent(
      `${window.location.origin}/action/macro/host1-talk-prod`,
    );
  });
});

describe("Audio setup → Seats (WP9b)", () => {
  it("maps a seat's microphone and in-ears, the production bus and microphones, and saves", async () => {
    await showSetup();
    openSection("Seats");
    const section = screen.getByTestId("seats-section");

    // Seat 2's microphone is channel 2, and its in-ears move to bus 3.
    fireEvent.change(within(section).getByLabelText("Seat 2 microphone"), {
      target: { value: "2" },
    });
    fireEvent.change(within(section).getByLabelText("Seat 2 in-ears"), {
      target: { value: "bus3" },
    });
    // Bus 6 becomes the production loop; channel 6 is a production microphone too.
    fireEvent.change(within(section).getByLabelText("Production bus"), {
      target: { value: "bus6" },
    });
    fireEvent.click(within(section).getByLabelText("Ch 6"));
    fireEvent.click(
      within(section).getByLabelText(
        "Hosts may change their own in-ear mix (otherwise the overlay is view-only)",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    const values = saves[0]!.values;
    expect(values.seats[1]).toEqual({ seat_id: 2, channel: "2", iem_bus: "bus3" });
    expect(values.production_bus).toBe("bus6");
    expect(values.production_channels).toEqual(["5", "6"]);
    expect(values.hosts_can_adjust_mix).toBe(false);
  });

  it("shows an unmapped seat as not mapped, and the hosts' names when hosts is on", async () => {
    await showSetup();
    act(() =>
      server.receive({
        type: "patch",
        module: "hosts",
        path: "",
        seq: 2,
        value: { seats: [{ id: 2, screen_name: "Bruno" }] },
      }),
    );
    openSection("Seats");

    expect(screen.getByLabelText("Seat 2 microphone")).toHaveValue("");
    expect(within(screen.getByTestId("seat-audio-table")).getByText("Bruno")).toBeInTheDocument();
  });
});
