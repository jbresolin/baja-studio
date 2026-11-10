/**
 * Tests for the hosts screen (src/pages/HostsScreen.tsx and components/hosts).
 *
 * A fake server plays the studio over the fake socket. Timers are fake, so
 * "four seconds without an answer" passes exactly when a test says so. The
 * screen's seats live in localStorage, as on the real hosts' PC.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HostsState } from "../src/lib/hosts";
import type { HostsView } from "../src/lib/playout";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { HostsScreen } from "../src/pages/HostsScreen";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";
import { hostsState, message } from "./hostsFixtures";

interface SentCommand {
  module: string;
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

let sockets: ReturnType<typeof fakeSocketFactory>;
let server: FakeSocket;

const NOTHING_ON_AIR: HostsView = { on_air: null, next: [], queue_remaining_s: 0 };

function tree(hosts: HostsState, extra: Record<string, unknown> = {}) {
  return {
    hosts,
    playout: { hosts_view: NOTHING_ON_AIR, export: { stream_start_marker: null } },
    ...extra,
  };
}

/** Open the screen (with these seats remembered, or none) on a fake server holding `state`. */
function showHostsScreen(state: Record<string, unknown>, rememberedSeats: number[] | null = [1]) {
  if (rememberedSeats !== null) {
    localStorage.setItem("baja-studio.hosts-seats", JSON.stringify(rememberedSeats));
  }
  sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <HostsScreen />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() => server.receive({ type: "snapshot", seq: 1, state, client_id: "monitor" }));
}

function sent(socket: FakeSocket = server): SentCommand[] {
  return (socket.sent as SentCommand[]).filter((message) => "command" in message);
}

function sentNamed(command: string, socket: FakeSocket = server): SentCommand[] {
  return sent(socket).filter((message) => message.command === command);
}

/** Answer every command not answered yet, as the server does, and let the page take the answers. */
async function answerAll(socket: FakeSocket = server) {
  await act(async () => {
    for (const command of sent(socket)) {
      socket.receive({ type: "reply", request_id: command.request_id, ok: true, result: {} });
    }
    socket.sent = [];
  });
}

/** A later change of the hosts state, as the server's patch. */
function patchHosts(seq: number, hosts: HostsState) {
  act(() => server.receive({ type: "patch", seq, module: "hosts", path: "", value: hosts }));
}

function advance(ms: number) {
  act(() => vi.advanceTimersByTime(ms));
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-01T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("seats", () => {
  it("asks which seats it is for on first open, remembers them and tells the server", () => {
    showHostsScreen(tree(hostsState()), null);

    // Only seats switched on are offered (the guests are off).
    expect(screen.getByRole("button", { name: "Ana" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Guest 1" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Bruno" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));

    expect(localStorage.getItem("baja-studio.hosts-seats")).toBe("[2]");
    expect(sentNamed("register_screen").at(-1)!.args).toEqual({ seats: [2] });
  });

  it("repeats which seats it shows every two seconds", () => {
    showHostsScreen(tree(hostsState()), [1, 2]);
    expect(sentNamed("register_screen")).toHaveLength(1);

    advance(2000);

    expect(sentNamed("register_screen")).toHaveLength(2);
    expect(sentNamed("register_screen")[1]!.args).toEqual({ seats: [1, 2] });
  });
});

describe("messages", () => {
  it("shows a message for this seat large and one for a colleague small", () => {
    const state = hostsState({
      messages: [
        message({ id: 1, to: 1, text: "30 s" }),
        message({ id: 2, to: 2, text: "wrap up" }),
      ],
    });
    showHostsScreen(tree(state), [1]);

    expect(screen.getByTestId("hosts-message-1")).toHaveAttribute("data-size", "large");
    expect(screen.getByTestId("hosts-message-2")).toHaveAttribute("data-size", "small");
    // Only the large one can be answered from this screen.
    expect(
      within(screen.getByTestId("hosts-message-2")).queryByRole("button", { name: "👍" }),
    ).toBeNull();
  });

  it("sends a reaction, a quick reply and a typed reply for this screen's seat", () => {
    showHostsScreen(tree(hostsState({ messages: [message({ id: 7, to: 1 })] })), [1]);
    const item = screen.getByTestId("hosts-message-7");

    fireEvent.click(within(item).getByRole("button", { name: "👍" }));
    fireEvent.click(within(item).getByRole("button", { name: "need 1 min" }));
    fireEvent.change(within(item).getByLabelText("Reply"), { target: { value: "one lap" } });
    fireEvent.keyDown(within(item).getByLabelText("Reply"), { key: "Enter" });

    expect(sentNamed("react").map((command) => command.args)).toEqual([
      { message_id: 7, seat_id: 1, reaction: "👍" },
      { message_id: 7, seat_id: 1, reaction: "need 1 min" },
    ]);
    expect(sentNamed("reply")[0]!.args).toEqual({ message_id: 7, seat_id: 1, text: "one lap" });
  });

  it("tells the server once which messages it showed to its seats (delivery)", () => {
    const state = hostsState({
      messages: [message({ id: 1, to: 1 }), message({ id: 2, to: 2 }), message({ id: 3 })],
    });
    showHostsScreen(tree(state), [1]);

    // Seat 1's message and the one for everyone; not Bruno's.
    expect(sentNamed("mark_shown").map((command) => command.args)).toEqual([
      { message_id: 1, seat_ids: [1] },
      { message_id: 3, seat_ids: [1] },
    ]);

    // The same state again (any patch) does not report them twice.
    patchHosts(2, { ...state, screens: 1 });
    expect(sentNamed("mark_shown")).toHaveLength(2);
  });

  it("pulses an urgent message until it is reacted to, and fades it after", () => {
    const urgent = message({ id: 1, to: 1, kind: "urgent", text: "go to break" });
    showHostsScreen(tree(hostsState({ messages: [urgent] })), [1]);
    expect(screen.getByTestId("hosts-message-1")).toHaveClass("hosts-message-pulse");

    patchHosts(
      2,
      hostsState({
        messages: [{ ...urgent, reactions: [{ seat_id: 1, reaction: "👍", ts: "" }] }],
      }),
    );

    expect(screen.getByTestId("hosts-message-1")).not.toHaveClass("hosts-message-pulse");
    expect(screen.getByTestId("hosts-message-1")).toHaveClass("hosts-message-faded");
  });
});

describe("cards", () => {
  it("counts down the clip on air and turns amber at ten seconds", () => {
    const view: HostsView = {
      on_air: { label: "Enduro · carro 42", remaining_s: 11, duration_s: 30 },
      next: [{ label: "Intro", duration_s: 20 }],
      queue_remaining_s: 20,
    };
    showHostsScreen(tree(hostsState(), { playout: { hosts_view: view, export: {} } }));

    expect(screen.getByTestId("hosts-countdown")).toHaveTextContent("−0:11");
    expect(screen.getByTestId("hosts-countdown")).not.toHaveClass("hosts-countdown-amber");

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "playout",
        path: "hosts_view.on_air.remaining_s",
        value: 10,
      }),
    );
    expect(screen.getByTestId("hosts-countdown")).toHaveClass("hosts-countdown-amber");
    expect(screen.getByRole("region", { name: "Next up" })).toHaveTextContent("Intro");
  });

  it("shows a quiet live badge when nothing is on air", () => {
    showHostsScreen(tree(hostsState()));

    expect(screen.getByRole("region", { name: "Now playing" })).toHaveTextContent("live");
  });

  it("moves a pushed card to the top", () => {
    showHostsScreen(tree(hostsState()));
    const titles = () =>
      screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"));
    expect(titles()[0]).toBe("Now playing");

    patchHosts(2, hostsState({ pushed_card: { id: "next_up", by: "web:prod1", at: "" } }));

    expect(titles()[0]).toBe("Next up");
  });

  it("shows each seat's microphone live or muted, and talkback to the camera op", () => {
    const audio = {
      mixer: { connection: "ok" },
      channels: { "1": { muted: false }, "2": { muted: true } },
      macros: { "prod1-talk-cam": { active: true } },
    };
    showHostsScreen(tree(hostsState(), { audio }));

    const card = screen.getByRole("region", { name: "Audio" });
    expect(card).toHaveTextContent("AnaLIVE");
    expect(card).toHaveTextContent("BrunoMUTED");
    expect(within(card).getByRole("status")).toHaveTextContent("Talkback → CAMERA OP");
  });

  it("shows the time since the stream started", () => {
    showHostsScreen(
      tree(hostsState(), {
        playout: {
          hosts_view: NOTHING_ON_AIR,
          export: { stream_start_marker: "2026-10-01T11:00:00+00:00" },
        },
      }),
    );

    expect(screen.getByTestId("stream-time")).toHaveTextContent("on air 1:00:00");
  });
});

describe("the stale border", () => {
  it("turns red when the connection drops and clears once the studio answers again", async () => {
    showHostsScreen(tree(hostsState()));
    await answerAll();
    expect(screen.getByTestId("hosts-screen")).not.toHaveClass("hosts-stale");

    act(() => server.drop());
    expect(screen.getByTestId("hosts-screen")).toHaveClass("hosts-stale");

    // The socket reconnects; the snapshot carries a message sent while we were away.
    advance(500);
    const again = sockets.latest();
    act(() => again.open());
    const missed = hostsState({ messages: [message({ id: 9, to: 1, text: "missed you" })] });
    act(() => again.receive({ type: "snapshot", seq: 40, state: tree(missed) }));
    await answerAll(again);

    expect(screen.getByTestId("hosts-screen")).not.toHaveClass("hosts-stale");
    expect(screen.getByText("missed you")).toBeInTheDocument();
  });

  it("turns red within five seconds when the studio stops answering on an open socket", async () => {
    // A pulled cable: the socket still says "open", but nothing comes back.
    showHostsScreen(tree(hostsState()));
    await answerAll();

    advance(4000);
    expect(screen.getByTestId("hosts-screen")).not.toHaveClass("hosts-stale");
    advance(1000);
    expect(screen.getByTestId("hosts-screen")).toHaveClass("hosts-stale");
  });
});
