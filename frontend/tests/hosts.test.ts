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
  streamTime,
  toggledLayout,
  valueAt,
} from "../src/lib/hosts";
import { hostsState, layout, message } from "./hostsFixtures";

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

  it("counts stream time from the marker as h:mm:ss", () => {
    expect(streamTime("2026-10-01T12:00:00Z", NOON + 3_725_000)).toBe("1:02:05");
    expect(streamTime(null, NOON)).toBeNull();
  });
});
