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
import { hostsState, message, seats } from "./hostsFixtures";

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

  it("switches a seat on and picks its microphone channel", () => {
    showHostsPage();

    fireEvent.click(screen.getByLabelText("Seat 3 on"));
    fireEvent.change(screen.getByLabelText("Seat 3 mic channel"), { target: { value: "9" } });

    expect(sentNamed("set_seat").map((command) => command.args)).toEqual([
      { seat_id: 3, enabled: true },
      { seat_id: 3, audio_channel: "9" },
    ]);
  });

  it("saves canned messages and quick replies one per line", () => {
    showHostsPage();
    const setup = screen.getByRole("region", { name: "Messages setup" });

    fireEvent.change(within(setup).getByLabelText("Canned messages (one per line)"), {
      target: { value: "10 s\n\nstretch\n" },
    });
    fireEvent.click(within(setup).getByRole("button", { name: "Save" }));

    expect(sentNamed("set_texts")[0]!.args).toEqual({
      canned_messages: ["10 s", "stretch"],
      quick_replies: ["ok", "need 1 min"],
      message_expires_s: 60,
      chime: false,
    });
  });
});
