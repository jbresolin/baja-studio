/**
 * Tests for the hosts helpers (src/lib/hosts.ts): which messages fade or
 * pulse, how big a message is on a screen, which cards a screen shows, and
 * the layout a drag or a tick sends to the server.
 */

import { describe, expect, it } from "vitest";

import {
  addressedSeats,
  cardsToShow,
  isFaded,
  isPulsing,
  loadScreenSeats,
  messageSize,
  movedLayout,
  saveScreenSeats,
  seatsFromUrl,
  streamTime,
  toggledLayout,
  valueAt,
  isExpanded,
  isFromHost,
  isHiddenOn,
  messageColor,
  senderSeat,
  senderTag,
  targetName,
  unreadFromHosts,
} from "../src/lib/hosts";
import { formatOffset, seatStateWord } from "../src/lib/seatAudio";
import { hostMessage, hostsState, layout, message } from "./hostsFixtures";

const NOON = Date.parse("2026-10-01T12:00:00Z");

describe("fading and pulsing", () => {
  it("fades a message once it is answered, dismissed or expired, and not before", () => {
    const fresh = message({ id: 1, expires_at: "2026-10-01T12:01:00+00:00" });
    expect(isFaded(fresh, NOON + 30_000)).toBe(false);

    // A minute later it has expired.
    expect(isFaded(fresh, NOON + 60_000)).toBe(true);
    // A reaction, a reply or a dismissal fades it at once.
    const reacted = { ...fresh, reactions: [{ seat_id: 1 as const, reaction: "👍", ts: "" }] };
    expect(isFaded(reacted, NOON)).toBe(true);
    expect(isFaded({ ...fresh, reply_text: "ok" }, NOON)).toBe(true);
    expect(isFaded({ ...fresh, dismissed_at: "x" }, NOON)).toBe(true);
  });

  it("never expires a message without an expiry", () => {
    expect(isFaded(message({ id: 1 }), NOON + 86_400_000)).toBe(false);
  });

  it("pulses an urgent message until someone reacts", () => {
    const urgent = message({ id: 1, kind: "urgent" });
    expect(isPulsing(urgent)).toBe(true);
    expect(isPulsing({ ...urgent, reactions: [{ seat_id: 2, reaction: "👍", ts: "" }] })).toBe(
      false,
    );
    expect(isPulsing(message({ id: 2, kind: "info" }))).toBe(false);
  });
});

describe("seat addressing", () => {
  it("shows a message large on its seat's screen and small on a colleague's", () => {
    const toBruno = message({ id: 1, to: 2 });

    expect(messageSize(toBruno, [2])).toBe("large");
    expect(messageSize(toBruno, [1])).toBe("small");
    // A screen two hosts share shows it large: one of them is addressed.
    expect(messageSize(toBruno, [1, 2])).toBe("large");
  });

  it("shows a message for everyone large everywhere, and every seat on the screen answers it", () => {
    const toAll = message({ id: 1, to: "all" });

    expect(messageSize(toAll, [1])).toBe("large");
    expect(addressedSeats(toAll, [1, 2])).toEqual([1, 2]);
    expect(addressedSeats(message({ id: 2, to: 2 }), [1, 2])).toEqual([2]);
  });
});

describe("cards on the screen", () => {
  it("shows visible cards in layout order with the pinned ones apart", () => {
    const { grid, pinned } = cardsToShow(hostsState());

    // The standings placeholder is hidden; messages is pinned under the grid.
    expect(grid.map((card) => card.id)).toEqual(["now_playing", "next_up", "audio", "clock"]);
    expect(pinned.map((card) => card.id)).toEqual(["messages"]);
  });

  it("puts a pushed card first, even one production hid", () => {
    const state = hostsState({
      pushed_card: { id: "standings", by: "web:prod1", at: "" },
    });

    expect(cardsToShow(state).grid[0]!.id).toBe("standings");
  });

  it("reads a card's value from any module's state by its dotted path", () => {
    const tree = { playout: { hosts_view: { next: [] } } };

    expect(valueAt(tree, "playout.hosts_view")).toEqual({ next: [] });
    expect(valueAt(tree, "audio.channels")).toBeUndefined();
    expect(valueAt(tree, null)).toBeUndefined();
  });
});

describe("layout changes", () => {
  it("moves a card and sends every floating card, pinned ones left out", () => {
    const moved = movedLayout(layout(), "clock", 0);

    expect(moved.map((entry) => entry.id)).toEqual([
      "clock",
      "now_playing",
      "next_up",
      "audio",
      "standings",
    ]);
  });

  it("shows or hides one card and keeps the rest as they are", () => {
    const toggled = toggledLayout(layout(), "standings", true);

    expect(toggled.find((entry) => entry.id === "standings")!.visible).toBe(true);
    expect(toggled.find((entry) => entry.id === "audio")!.visible).toBe(true);
  });
});

describe("the screen's seats and clocks", () => {
  it("remembers the seats in storage and ignores garbage", () => {
    saveScreenSeats(localStorage, [1, 2]);
    expect(loadScreenSeats(localStorage)).toEqual([1, 2]);

    localStorage.setItem("baja-studio.hosts-seats", "{not json");
    expect(loadScreenSeats(localStorage)).toBeNull();
    localStorage.clear();
    expect(loadScreenSeats(localStorage)).toBeNull();
  });

  it("takes the seats from the address, dropping ones that do not exist", () => {
    // How the rehearsal rig opens the screen: no picker, every seat.
    expect(seatsFromUrl("?seats=1,2,3,4")).toEqual([1, 2, 3, 4]);
    expect(seatsFromUrl("?seats=2, 2,9,x")).toEqual([2]);
    // No seats named (or none valid): the screen falls back to storage and the picker.
    expect(seatsFromUrl("")).toBeNull();
    expect(seatsFromUrl("?seats=9")).toBeNull();
  });

  it("counts stream time from the marker as h:mm:ss", () => {
    expect(streamTime("2026-10-01T12:00:00Z", NOON + 3_725_000)).toBe("1:02:05");
    expect(streamTime(null, NOON)).toBeNull();
  });
});

describe("compact chat, hiding and hosts' messages (WP9b)", () => {
  it("grows a message for this screen until it is answered, then for four seconds more", () => {
    const cue = message({ id: 1, to: 1 });
    expect(isExpanded(cue, [1], NOON + 30_000)).toBe(true);

    const answered = {
      ...cue,
      reactions: [{ seat_id: 1 as const, reaction: "👍", ts: "2026-10-01T12:00:10+00:00" }],
    };
    expect(isExpanded(answered, [1], NOON + 13_000)).toBe(true);
    expect(isExpanded(answered, [1], NOON + 14_000)).toBe(false);
  });

  it("never grows a colleague's message, an expired one or one production waved away", () => {
    expect(isExpanded(message({ id: 1, to: 2 }), [1], NOON)).toBe(false);
    const expiring = message({ id: 2, to: 1, expires_at: "2026-10-01T12:01:00+00:00" });
    expect(isExpanded(expiring, [1], NOON + 61_000)).toBe(false);
    expect(isExpanded(message({ id: 3, to: 1, dismissed_at: "x" }), [1], NOON)).toBe(false);
  });

  it("grows a host's own message for a moment when production answers it", () => {
    const own = hostMessage({ id: 4, seat: 1, reply_text: "ok", reply_at: "2026-10-01T12:00:00Z" });
    expect(isExpanded(own, [1], NOON + 1_000)).toBe(true);
    expect(isExpanded(own, [1], NOON + 5_000)).toBe(false);
    // A colleague's message to production never grows on this screen.
    expect(isExpanded(own, [2], NOON + 1_000)).toBe(false);
  });

  it("hides a message on a screen when one of its seats pressed ✕", () => {
    const hidden = message({ id: 1, dismissed_by: [{ seat_id: 2, ts: "" }] });
    expect(isHiddenOn(hidden, [2])).toBe(true);
    expect(isHiddenOn(hidden, [1, 2])).toBe(true);
    expect(isHiddenOn(hidden, [1])).toBe(false);
  });

  it("knows a host's message by its sender and target, and which are unread", () => {
    const fromBruno = hostMessage({ id: 2, seat: 2 });
    const read = hostMessage({ id: 3, seat: 1, read_at: "x" });
    expect(senderSeat(fromBruno)).toBe(2);
    expect(senderSeat(message({ id: 1 }))).toBeNull();
    expect(isFromHost(fromBruno)).toBe(true);
    // Production's messages are never "unread".
    expect(unreadFromHosts([message({ id: 1 }), fromBruno, read])).toEqual([fromBruno]);
    // A host's message is for production: no seat on a screen is addressed by it.
    expect(addressedSeats(fromBruno, [1, 2])).toEqual([]);
  });

  it("names and colours a host's message after its sender seat", () => {
    const seats = hostsState().seats;
    const fromBruno = hostMessage({ id: 2, seat: 2 });
    expect(senderTag(fromBruno.from, seats)).toBe("Bruno");
    expect(messageColor(fromBruno, seats)).toBe("#e67e22");
    expect(targetName("production", seats)).toBe("production");
  });
});

describe("personal mix helpers (WP9b)", () => {
  it("writes offsets with a sign and a real minus", () => {
    expect(formatOffset(2)).toBe("+2 dB");
    expect(formatOffset(-1.5)).toBe("−1.5 dB");
    expect(formatOffset(0)).toBe("0 dB");
    expect(formatOffset(0.04)).toBe("0 dB");
  });

  it("says what the card shows for each state, and … while unknown", () => {
    expect(seatStateWord("to_production")).toBe("TO PRODUCTION");
    expect(seatStateWord("not_mapped")).toBe("not mapped");
    expect(seatStateWord(null)).toBe("…");
  });
});
