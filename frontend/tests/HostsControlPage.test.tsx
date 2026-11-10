/**
 * Tests for production's Hosts page (src/pages/HostsControlPage.tsx and
 * components/hosts). A fake server sends the hosts state; the tests read the
 * commands the page sent.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { HostsState } from "../src/lib/hosts";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { HostsControlPage } from "../src/pages/HostsControlPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";
import { hostMessage, hostsState, message, seats } from "./hostsFixtures";

interface SentCommand {
  module: string;
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

let server: FakeSocket;

function showHostsPage(hosts: HostsState = hostsState()) {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <HostsControlPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() => server.receive({ type: "snapshot", seq: 1, state: { hosts }, client_id: "prod1" }));
}

function sentNamed(command: string): SentCommand[] {
  return (server.sent as SentCommand[]).filter((message) => message.command === command);
}

beforeEach(() => {
  localStorage.clear();
});

describe("the seat strip", () => {
  it("shows the seats in use and whether a hosts screen shows each", () => {
    const withAnaOnline = seats().map((seat) =>
      seat.id === 1 ? { ...seat, online: true, screens: 1 } : seat,
    );
    showHostsPage(hostsState({ seats: withAnaOnline }));

    expect(screen.getByTestId("seat-chip-1")).toHaveTextContent("Ana online");
    expect(screen.getByTestId("seat-chip-2")).toHaveTextContent("Bruno offline");
    // The guests are switched off, so they are not in the strip.
    expect(screen.queryByTestId("seat-chip-3")).toBeNull();
  });
});

describe("sending", () => {
  it("sends a typed message to the chosen seat with the chosen kind", () => {
    showHostsPage();
    const composer = screen.getByRole("form", { name: "Message hosts" });

    fireEvent.change(within(composer).getByLabelText("To"), { target: { value: "2" } });
    fireEvent.change(within(composer).getByLabelText("Kind"), { target: { value: "urgent" } });
    fireEvent.change(within(composer).getByLabelText("Message"), {
      target: { value: "go to break" },
    });
    fireEvent.click(within(composer).getByRole("button", { name: "Send" }));

    expect(sentNamed("send_message")[0]!.args).toEqual({
      to: 2,
      text: "go to break",
      kind: "urgent",
    });
  });

  it("sends a canned message with one tap, to everyone by default", () => {
    showHostsPage();

    fireEvent.click(screen.getByRole("button", { name: "30 s" }));

    expect(sentNamed("send_message")[0]!.args).toEqual({ to: "all", text: "30 s", kind: "info" });
  });
});

describe("the history", () => {
  it("shows delivery and the reactions and reply as they arrive", () => {
    const sent = message({ id: 4, to: 2, text: "wrap up" });
    showHostsPage(hostsState({ messages: [sent] }));
    expect(screen.getByTestId("delivery-4")).toHaveTextContent("not shown yet");

    const answered = {
      ...sent,
      shown: [{ seat_id: 2 as const, ts: "" }],
      reactions: [{ seat_id: 2 as const, reaction: "👍", ts: "" }],
      reply_text: "30 more seconds",
      reply_seat_id: 2 as const,
    };
    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "hosts",
        path: "messages",
        value: [answered],
      }),
    );

    expect(screen.getByTestId("delivery-4")).toHaveTextContent("shown: Bruno");
    expect(screen.getByTestId("reactions-4")).toHaveTextContent("👍 Bruno");
    expect(screen.getByTestId("history-4")).toHaveTextContent("“30 more seconds” — Bruno");
    // Answered, so it fades.
    expect(screen.getByTestId("history-4")).toHaveClass("faded");
  });

  it("dismisses a message", () => {
    showHostsPage(hostsState({ messages: [message({ id: 4 })] }));

    fireEvent.click(
      within(screen.getByTestId("history-4")).getByRole("button", { name: "Dismiss" }),
    );

    expect(sentNamed("dismiss")[0]!.args).toEqual({ message_id: 4 });
  });
});

describe("cards", () => {
  it("pushes a card to the top of the hosts screens and unpushes it", () => {
    showHostsPage();

    fireEvent.click(screen.getByRole("button", { name: "Push Next up" }));
    expect(sentNamed("push_card")[0]!.args).toEqual({ card_id: "next_up" });

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "hosts",
        path: "pushed_card",
        value: { id: "next_up", by: "web:prod1", at: "" },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Unpush" }));
    expect(sentNamed("push_card")[1]!.args).toEqual({ card_id: null });
  });

  it("moves a card and shows a hidden one by sending the whole layout", () => {
    showHostsPage();

    fireEvent.click(screen.getByRole("button", { name: "Move Clock up" }));
    fireEvent.click(within(screen.getByTestId("layout-standings")).getByRole("checkbox"));

    const [moved, shown] = sentNamed("set_card_layout").map((command) => command.args.cards);
    expect((moved as { id: string }[]).map((entry) => entry.id)).toEqual([
      "now_playing",
      "next_up",
      "clock",
      "audio",
      "standings",
    ]);
    expect(shown).toContainEqual({ id: "standings", visible: true });
    // The messages card is always shown, so it is not offered.
    expect(screen.queryByTestId("layout-messages")).toBeNull();
  });
});

describe("setup", () => {
  it("renames a seat when the field is left", () => {
    showHostsPage();
    const name = screen.getByLabelText("Seat 1 name");

    fireEvent.change(name, { target: { value: "Carla" } });
    fireEvent.blur(name);

    expect(sentNamed("set_seat_name")[0]!.args).toEqual({ seat_id: 1, screen_name: "Carla" });
  });

  it("switches a seat on, and sends the microphone to the audio setup", () => {
    showHostsPage();

    fireEvent.click(screen.getByLabelText("Seat 3 on"));

    expect(sentNamed("set_seat").map((command) => command.args)).toEqual([
      { seat_id: 3, enabled: true },
    ]);
    // The seat's microphone and in-ears are audio facts now (WP9b): one place per setting.
    expect(screen.queryByLabelText("Seat 3 mic channel")).toBeNull();
    expect(screen.getByRole("link", { name: "Audio setup → Seats" })).toHaveAttribute(
      "href",
      "/audio/config",
    );
  });

  it("saves canned messages, quick replies and the hosts' phrases one per line", () => {
    showHostsPage();
    const setup = screen.getByRole("region", { name: "Messages setup" });

    fireEvent.change(within(setup).getByLabelText("Canned messages (one per line)"), {
      target: { value: "10 s\n\nstretch\n" },
    });
    fireEvent.change(
      within(setup).getByLabelText("Hosts' one-tap messages to production (one per line)"),
      { target: { value: "pronto\nmais 1 min" } },
    );
    fireEvent.click(within(setup).getByLabelText("Chime on this page when a host writes"));
    fireEvent.click(within(setup).getByRole("button", { name: "Save" }));

    expect(sentNamed("set_texts")[0]!.args).toEqual({
      canned_messages: ["10 s", "stretch"],
      quick_replies: ["ok", "need 1 min"],
      message_expires_s: 60,
      chime: false,
      host_phrases: ["pronto", "mais 1 min"],
      production_chime: false,
    });
  });

  it("applies the chat area size at once, without Save", () => {
    showHostsPage();

    fireEvent.change(screen.getByLabelText("Chat area size"), { target: { value: "large" } });

    // Production tries it while looking at the hosts screen.
    expect(sentNamed("set_texts")[0]!.args).toEqual({ chat_size: "large" });
  });
});

describe("messages from the hosts", () => {
  it("shows a host's message in the seat's colour, unread, with a count by the title", () => {
    showHostsPage(hostsState({ messages: [hostMessage({ id: 4, seat: 2, text: "repete?" })] }));

    const item = screen.getByTestId("history-4");
    expect(item).toHaveTextContent("← Bruno");
    expect(item).toHaveStyle({ borderColor: "#e67e22" });
    expect(screen.getByTestId("unread-4")).toHaveTextContent("new");
    expect(screen.getByTestId("unread-count")).toHaveTextContent("1 new from the hosts");
  });

  it("marks the hosts' messages read", () => {
    showHostsPage(
      hostsState({
        messages: [hostMessage({ id: 4, seat: 2 }), hostMessage({ id: 5, seat: 1 })],
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Mark read" }));

    expect(sentNamed("mark_read")[0]!.args).toEqual({ message_ids: [4, 5] });
  });

  it("reacts and replies to a host's message as production (no seat)", () => {
    showHostsPage(hostsState({ messages: [hostMessage({ id: 4, seat: 2 })] }));

    fireEvent.click(screen.getByRole("button", { name: "React 👍 to message 4" }));
    const reply = screen.getByLabelText("Reply to message 4");
    fireEvent.change(reply, { target: { value: "ok, 30 s" } });
    fireEvent.keyDown(reply, { key: "Enter" });

    expect(sentNamed("react")[0]!.args).toEqual({ message_id: 4, reaction: "👍" });
    expect(sentNamed("reply")[0]!.args).toEqual({ message_id: 4, text: "ok, 30 s" });
  });

  it("shows which seats hid production's message on their screen", () => {
    const hidden = message({ id: 2, to: "all", dismissed_by: [{ seat_id: 1, ts: "" }] });
    showHostsPage(hostsState({ messages: [hidden] }));

    expect(screen.getByTestId("dismissed-by-2")).toHaveTextContent("dismissed by Ana");
  });

  it("chimes for a new host message only when production wants it, not on opening", () => {
    const played: number[] = [];
    class FakeAudioContext {
      currentTime = 0;
      destination = {};
      createOscillator() {
        played.push(1);
        return { frequency: {}, connect: () => ({ connect: () => {} }), start() {}, stop() {} };
      }
      createGain() {
        return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} } };
      }
      close() {
        return Promise.resolve();
      }
    }
    const original = window.AudioContext;
    window.AudioContext = FakeAudioContext as unknown as typeof AudioContext;
    try {
      // Opening with an old message: no sound.
      showHostsPage(hostsState({ messages: [hostMessage({ id: 1, seat: 1 })] }));
      expect(played).toHaveLength(0);

      // A host writes: two notes.
      const messages = [hostMessage({ id: 1, seat: 1 }), hostMessage({ id: 2, seat: 2 })];
      act(() =>
        server.receive({
          type: "patch",
          seq: 2,
          module: "hosts",
          path: "messages",
          value: messages,
        }),
      );
      expect(played).toHaveLength(2);
    } finally {
      window.AudioContext = original;
    }
  });
});
