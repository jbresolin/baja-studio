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
import { seatAudioView } from "./audioFixtures";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";
import { hostMessage, hostsState, message } from "./hostsFixtures";

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

  it("takes its seats from the address without asking, and remembers them", () => {
    // The rehearsal rig opens /hosts?seats=1,2 in a browser that never chose seats.
    window.history.pushState({}, "", "/hosts?seats=1,2");
    try {
      showHostsScreen(tree(hostsState()), null);
    } finally {
      window.history.pushState({}, "", "/");
    }

    expect(screen.queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
    expect(sentNamed("register_screen").at(-1)!.args).toEqual({ seats: [1, 2] });
    expect(localStorage.getItem("baja-studio.hosts-seats")).toBe("[1,2]");
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

  it("shows where each seat's microphone is heard, production → you, and talkback", () => {
    // Audio publishes the facts; the names and colours are the hosts' seats.
    const view = seatAudioView({
      talkback: { active: true, label: "CAMERA OP" },
      seats: {
        ...seatAudioView().seats,
        "1": { ...seatAudioView().seats["1"]!, state: "live", production_to_you: true },
        "2": { ...seatAudioView().seats["2"]!, state: "to_production" },
      },
    });
    showHostsScreen(tree(hostsState(), { audio: { hosts_view: view } }));

    const card = screen.getByRole("region", { name: "Audio" });
    expect(screen.getByTestId("seat-audio-1")).toHaveTextContent("AnaLIVEproduction → you");
    expect(screen.getByTestId("seat-audio-2")).toHaveTextContent("BrunoTO PRODUCTION");
    expect(within(card).getByRole("status")).toHaveTextContent("Talkback → CAMERA OP");
  });

  it("says the mixer is offline instead of showing old states", () => {
    const view = seatAudioView({ mixer_online: false });
    showHostsScreen(tree(hostsState(), { audio: { hosts_view: view } }));

    const card = screen.getByRole("region", { name: "Audio" });
    expect(card).toHaveTextContent("Mixer offline");
    expect(card).not.toHaveTextContent("LIVE");
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

describe("hiding a message (WP9b)", () => {
  it("the ✕ hides the message itself, not only the ✕, and says so for this screen's seat", () => {
    // The bench: pressing ✕ removed the ✕ and left the message on screen.
    showHostsScreen(tree(hostsState({ messages: [message({ id: 3, to: 1, text: "30 s" })] })), [1]);

    fireEvent.click(
      within(screen.getByTestId("hosts-message-3")).getByRole("button", { name: "Dismiss" }),
    );

    expect(screen.queryByTestId("hosts-message-3")).toBeNull();
    expect(sentNamed("dismiss")[0]!.args).toEqual({ message_id: 3, seat_ids: [1] });
  });

  it("stays hidden after a reload, because the server keeps it per seat", () => {
    const hidden = message({ id: 3, to: "all", dismissed_by: [{ seat_id: 1, ts: "" }] });
    // Seat 1's screen (reloaded): gone. Seat 2's screen: still there.
    showHostsScreen(tree(hostsState({ messages: [hidden] })), [1]);
    expect(screen.queryByTestId("hosts-message-3")).toBeNull();
  });

  it("does not hide it on another seat's screen", () => {
    const hidden = message({ id: 3, to: "all", dismissed_by: [{ seat_id: 1, ts: "" }] });
    showHostsScreen(tree(hostsState({ messages: [hidden] })), [2]);
    expect(screen.getByTestId("hosts-message-3")).toBeInTheDocument();
  });
});

describe("compact chat (WP9b)", () => {
  it("grows for a new message to this seat and shrinks back a few seconds after an answer", () => {
    const cue = message({ id: 1, to: 1, text: "30 s" });
    showHostsScreen(tree(hostsState({ messages: [cue] })), [1]);
    expect(screen.getByTestId("hosts-message-1")).toHaveAttribute("data-expanded", "true");
    expect(screen.getByTestId("hosts-chat")).toHaveAttribute("data-growing", "true");

    // Ana taps 👍 at 12:00:01; four seconds later it is one line again.
    const answered = {
      ...cue,
      reactions: [{ seat_id: 1 as const, reaction: "👍", ts: "2026-10-01T12:00:01+00:00" }],
    };
    patchHosts(2, hostsState({ messages: [answered] }));
    advance(1000);
    expect(screen.getByTestId("hosts-message-1")).toHaveAttribute("data-expanded", "true");
    advance(5000);
    expect(screen.getByTestId("hosts-message-1")).toHaveAttribute("data-expanded", "false");
    expect(screen.getByTestId("hosts-chat")).toHaveAttribute("data-growing", "false");
  });

  it("keeps an urgent message grown until someone reacts", () => {
    const urgent = message({ id: 1, to: 1, kind: "urgent", text: "go to break" });
    showHostsScreen(tree(hostsState({ messages: [urgent] })), [1]);

    advance(60_000);

    expect(screen.getByTestId("hosts-message-1")).toHaveAttribute("data-expanded", "true");
  });

  it("shows a colleague's message as one small line", () => {
    showHostsScreen(tree(hostsState({ messages: [message({ id: 2, to: 2 })] })), [1]);

    expect(screen.getByTestId("hosts-message-2")).toHaveAttribute("data-expanded", "false");
    expect(screen.getByTestId("hosts-message-2")).toHaveAttribute("data-size", "small");
  });

  it("takes the chat size production picked, and follows a change at once", () => {
    showHostsScreen(tree(hostsState()), [1]);
    expect(screen.getByTestId("hosts-chat")).toHaveAttribute("data-chat-size", "small");

    const large = hostsState();
    large.config = { ...large.config, chat_size: "large" };
    patchHosts(2, large);

    expect(screen.getByTestId("hosts-chat")).toHaveClass("hosts-chat-large");
  });
});

describe("writing to production (WP9b)", () => {
  it("sends a one-tap phrase and a typed urgent message from this screen's seat", () => {
    showHostsScreen(tree(hostsState()), [1]);
    const composer = screen.getByRole("form", { name: "Message production" });

    fireEvent.click(within(composer).getByRole("button", { name: "pronto" }));
    fireEvent.change(within(composer).getByLabelText("Message to production"), {
      target: { value: "problema no áudio" },
    });
    fireEvent.click(within(composer).getByLabelText("urgent"));
    fireEvent.click(within(composer).getByRole("button", { name: "Send" }));

    expect(sentNamed("send_host_message").map((command) => command.args)).toEqual([
      { seat_id: 1, text: "pronto", kind: "info" },
      { seat_id: 1, text: "problema no áudio", kind: "urgent" },
    ]);
  });

  it("lets a shared screen pick which seat is writing", () => {
    showHostsScreen(tree(hostsState()), [1, 2]);
    const composer = screen.getByRole("form", { name: "Message production" });

    fireEvent.change(within(composer).getByLabelText("From"), { target: { value: "2" } });
    fireEvent.click(within(composer).getByRole("button", { name: "pronto" }));

    expect(sentNamed("send_host_message")[0]!.args).toMatchObject({ seat_id: 2 });
  });

  it("shows the host's own message with production's reply, not a colleague's", () => {
    const replied = hostMessage({
      id: 5,
      seat: 1,
      text: "repete?",
      reply_text: "30 s to break",
      reply_by: "production:prod1",
      reply_at: "2026-10-01T12:00:00+00:00",
    });
    const colleagues = hostMessage({ id: 6, seat: 2, text: "pronto" });
    showHostsScreen(tree(hostsState({ messages: [replied, colleagues] })), [1]);

    expect(screen.getByTestId("hosts-message-5")).toHaveTextContent("Ana → production");
    expect(screen.getByTestId("hosts-message-5")).toHaveTextContent("“30 s to break” — production");
    expect(screen.queryByTestId("hosts-message-6")).toBeNull();
  });
});

describe("personal mix (WP9b)", () => {
  function showWithMix(view = seatAudioView()) {
    showHostsScreen(tree(hostsState(), { audio: { hosts_view: view } }), [1]);
  }

  it("opens this seat's mix from the audio card, with main levels and offsets", () => {
    showWithMix();

    fireEvent.click(screen.getByRole("button", { name: /Ana: LIVE, open the in-ear mix/ }));

    const overlay = screen.getByRole("dialog", { name: "Ana's in-ear mix" });
    expect(within(overlay).getByTestId("mix-row-9")).toHaveTextContent("SHOTGUNmain 0.0 dB +2 dB");
    // PC AUDIO is not linked to Ana's bus: its own level, labelled independent.
    expect(within(overlay).getByTestId("mix-row-aux")).toHaveTextContent(
      "PC AUDIOindependent -10.0 dB",
    );
    // The card stays on screen underneath.
    expect(screen.getByRole("region", { name: "Audio" })).toBeInTheDocument();
  });

  it("does not offer another seat's mix", () => {
    showWithMix();
    expect(screen.queryByRole("button", { name: /Bruno: .*open the in-ear mix/ })).toBeNull();
  });

  it("steps an offset with + and −, repeats while held, and resets to main", () => {
    showWithMix();
    fireEvent.click(screen.getByRole("button", { name: /open the in-ear mix/ }));
    const up = screen.getByRole("button", { name: "SHOTGUN up" });

    fireEvent.pointerDown(up);
    advance(400 + 250); // the first repeat, then one more
    fireEvent.pointerUp(up);
    advance(1000); // released: nothing more
    fireEvent.pointerDown(screen.getByRole("button", { name: "SHOTGUN down" }));
    fireEvent.pointerUp(screen.getByRole("button", { name: "SHOTGUN down" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset to main" }));

    const steps = sentNamed("set_seat_offset").map((command) => command.args);
    expect(steps).toEqual([
      { seat_id: 1, channel: "9", delta_db: 1 },
      { seat_id: 1, channel: "9", delta_db: 1 },
      { seat_id: 1, channel: "9", delta_db: 1 },
      { seat_id: 1, channel: "9", delta_db: -1 },
    ]);
    expect(sentNamed("set_seat_offset").every((command) => command.module === "audio")).toBe(true);
    expect(sentNamed("reset_seat_offsets")[0]!.args).toEqual({ seat_id: 1 });
  });

  it("closes by itself after twenty seconds without a touch", () => {
    showWithMix();
    fireEvent.click(screen.getByRole("button", { name: /open the in-ear mix/ }));

    advance(19_000);
    fireEvent.pointerDown(screen.getByRole("dialog")); // a touch restarts the wait
    advance(19_000);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    advance(1500);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("is view-only when production says so, and disabled with the mixer offline", () => {
    showWithMix(seatAudioView({ can_adjust: false }));
    fireEvent.click(screen.getByRole("button", { name: /open the in-ear mix/ }));
    expect(screen.getByRole("button", { name: "SHOTGUN up" })).toBeDisabled();
    expect(screen.getByRole("dialog")).toHaveTextContent("View only");

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "audio",
        path: "hosts_view.mixer_online",
        value: false,
      }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("Mixer offline");
    expect(screen.getByRole("button", { name: "Reset to main" })).toBeDisabled();
  });

  it("shows a toast when a pad changes this seat's mix, but not for what was there before", () => {
    const before = { seq: 4, seat_id: 1, channel: "9", label: "SHOTGUN", offset_db: 1 };
    showWithMix(seatAudioView({ pad_change: before }));
    expect(screen.queryByTestId("pad-toast")).toBeNull();

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "audio",
        path: "hosts_view.pad_change",
        value: { ...before, seq: 5, offset_db: 2 },
      }),
    );
    expect(screen.getByTestId("pad-toast")).toHaveTextContent("Ana: SHOTGUN +2 dB");

    advance(3500);
    expect(screen.queryByTestId("pad-toast")).toBeNull();
  });

  it("does not toast a change to another seat's mix", () => {
    showWithMix();
    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "audio",
        path: "hosts_view.pad_change",
        value: { seq: 1, seat_id: 2, channel: "9", label: "SHOTGUN", offset_db: 3 },
      }),
    );
    expect(screen.queryByTestId("pad-toast")).toBeNull();
  });
});
