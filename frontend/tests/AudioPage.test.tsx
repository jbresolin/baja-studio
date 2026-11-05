/**
 * Tests for the Audio page (src/pages/AudioPage.tsx and components/audio/).
 *
 * The page reads its labels, macros and scenes from the audio settings over
 * HTTP (`fetch`, faked here), and live state and commands over the
 * WebSocket (a fake socket the test plays).
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AudioState } from "../src/lib/audio";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { AudioPage } from "../src/pages/AudioPage";
import { audioSettings, audioState } from "./audioFixtures";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

let server: FakeSocket;

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ section: "audio", version: 4, updated_at: "", values: audioSettings() }),
    ),
  );
});

afterEach(() => vi.unstubAllGlobals());

interface SentCommand {
  command: string;
  args: Record<string, unknown>;
}

async function showAudioPage(state: AudioState = audioState()) {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <AudioPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() => server.receive({ type: "snapshot", seq: 1, state: { audio: state } }));
  await screen.findByTestId("strips");
}

function commands(): SentCommand[] {
  return server.sent as SentCommand[];
}

describe("Audio page", () => {
  it("shows the visible channels with their labels, levels and mutes", async () => {
    await showAudioPage();

    const host = screen.getByTestId("strip-1");
    expect(within(host).getByText("HOST 1")).toBeInTheDocument();
    expect(within(host).getByText("0.0 dB")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("strip-5")).getByRole("button", { name: "Muted" }),
    ).toHaveAttribute("aria-pressed", "true");
    // Channel 2 is not visible in the setup.
    expect(screen.queryByTestId("strip-2")).toBeNull();
  });

  it("mute toggles through the server", async () => {
    await showAudioPage();

    fireEvent.click(within(screen.getByTestId("strip-1")).getByRole("button", { name: "Mute" }));

    expect(commands().at(-1)).toMatchObject({ command: "set_mute", args: { channel: "1" } });
  });

  it("the fader moves by 1 dB with the arrow keys", async () => {
    await showAudioPage();

    fireEvent.keyDown(screen.getByRole("slider", { name: "HOST 1 fader" }), { key: "ArrowUp" });

    const sent = commands().at(-1)!;
    expect(sent.command).toBe("set_fader");
    expect(sent.args.channel).toBe("1");
    expect(sent.args.level as number).toBeCloseTo(0.775); // +1 dB from unity
    expect(sent.args.dragging).toBeUndefined();
  });

  it("a bus view turns the faders into sends, reads them, and offers relink when paused", async () => {
    await showAudioPage();

    fireEvent.click(screen.getByRole("tab", { name: "IEM HOST 1" }));

    expect(commands().at(-1)).toMatchObject({ command: "request_sends", args: { bus: "bus1" } });
    fireEvent.keyDown(screen.getByRole("slider", { name: "HOST 1 send" }), { key: "ArrowDown" });
    expect(commands().at(-1)).toMatchObject({
      command: "set_aux",
      args: { channel: "1", bus: "bus1" },
    });

    fireEvent.click(within(screen.getByTestId("strip-5")).getByRole("button", { name: /relink/ }));
    expect(commands().at(-1)).toMatchObject({
      command: "set_link",
      args: { channel: "5", bus: "bus1", state: "linked" },
    });
  });

  it("macro buttons are lit while active and run on a tap", async () => {
    await showAudioPage();

    const grid = screen.getByTestId("macro-grid");
    expect(within(grid).getByRole("button", { name: "Prod 1 → Hosts" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(within(grid).getByRole("button", { name: "Host 1 → Prod" }));

    expect(commands().at(-1)).toMatchObject({
      command: "run_macro",
      args: { macro: "host1-talk-prod" },
    });
  });

  it("a scene with a snapshot asks first, because it replaces the whole mix", async () => {
    await showAudioPage();

    fireEvent.click(screen.getByRole("button", { name: "1. Race" }));

    expect(screen.getByRole("alertdialog")).toHaveTextContent("changes everything on the mixer");
    expect(commands().some((sent) => sent.command === "recall_scene")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Recall" }));
    expect(commands().at(-1)).toMatchObject({ command: "recall_scene", args: { index: 1 } });
  });

  it("with the mixer offline the controls are off and the reason is shown", async () => {
    const state = audioState();
    state.mixer = {
      ...state.mixer,
      connection: "offline",
      message: "XR18 at 192.168.1.10 is not answering",
    };
    await showAudioPage(state);

    expect(screen.getByTestId("audio-status")).toHaveTextContent("not answering");
    expect(
      within(screen.getByTestId("strip-1")).getByRole("button", { name: "Mute" }),
    ).toBeDisabled();
  });

  it("the M-VAVE knobs' bus is shared and set with a command", async () => {
    await showAudioPage();

    fireEvent.change(screen.getByLabelText("M-VAVE knobs turn"), { target: { value: "bus1" } });

    expect(commands().at(-1)).toMatchObject({ command: "select_bus", args: { bus: "bus1" } });
  });
});
