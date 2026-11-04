/**
 * Tests for the Playout page's pure helpers (src/lib/playout.ts): the
 * countdown colours, the keyboard map and the library grouping.
 */

import { describe, expect, it } from "vitest";

import {
  countdownColour,
  formatDuration,
  groupLibrary,
  playoutKeyAction,
} from "../src/lib/playout";
import { clip } from "./playoutFixtures";

describe("countdownColour", () => {
  it("is white with more than ten seconds left, amber from ten, red from five", () => {
    // The thresholds are inclusive: exactly 10 s left is already amber, 5 s red.
    expect(countdownColour(11)).toBe("white");
    expect(countdownColour(10)).toBe("amber");
    expect(countdownColour(6)).toBe("amber");
    expect(countdownColour(5)).toBe("red");
    expect(countdownColour(0)).toBe("red");
  });

  it("is white when the remaining time is not known yet", () => {
    expect(countdownColour(null)).toBe("white");
  });
});

describe("formatDuration", () => {
  it("writes minutes and seconds, hours only when needed, and a dash when unknown", () => {
    expect(formatDuration(65.9)).toBe("1:05");
    expect(formatDuration(3725)).toBe("1:02:05");
    expect(formatDuration(null)).toBe("–");
  });
});

describe("playoutKeyAction", () => {
  const press = (key: string, extra: Record<string, boolean> = {}) =>
    playoutKeyAction({ key, targetIsEditable: false, ...extra });

  it("maps the operator keys to their actions", () => {
    expect(press(" ")).toBe("take");
    expect(press("ArrowRight")).toBe("skip");
    expect(press("ArrowRight", { shiftKey: true })).toBe("drop_next");
    expect(press("Backspace")).toBe("drop_next");
    expect(press("r")).toBe("return");
    expect(press("R")).toBe("return");
    expect(press("l")).toBe("replay_last");
    expect(press("Delete")).toBe("remove_selected");
    expect(press("/")).toBe("focus_search");
    expect(press("?")).toBe("toggle_legend");
    expect(press("x")).toBeNull();
  });

  it("ignores every key while the operator types in a field", () => {
    // Typing "r" in the notes box must not take the clip off air.
    expect(playoutKeyAction({ key: "r", targetIsEditable: true })).toBeNull();
    expect(playoutKeyAction({ key: " ", targetIsEditable: true })).toBeNull();
  });

  it("ignores keys pressed with Ctrl, Alt or Meta (browser shortcuts)", () => {
    expect(press("r", { ctrlKey: true })).toBeNull();
    expect(press("l", { metaKey: true })).toBeNull();
  });
});

describe("groupLibrary", () => {
  // Two pipeline clips in two provas, one manual clip, one missing file, one played.
  const library = {
    "1": clip({ id: 1, title: "Intro" }),
    "2": clip({
      id: 2,
      source: "orchestrator",
      title: "Enduro · carro 42",
      prova: "Enduro",
      car_number: 42,
      passagem: 3,
    }),
    "3": clip({
      id: 3,
      source: "orchestrator",
      title: "Enduro · carro 7",
      prova: "Enduro",
      car_number: 7,
      passagem: 1,
      played_count: 2,
    }),
    "4": clip({
      id: 4,
      source: "orchestrator",
      title: "Suspensão · carro 9",
      prova: "Suspensão",
      car_number: 9,
      missing: true,
      queueable: false,
    }),
  };

  it("groups by prova alphabetically, manual clips last, cars in number order", () => {
    const groups = groupLibrary(library, "", "all");

    expect(groups.map((group) => group.prova)).toEqual(["Enduro", "Suspensão", "Manual"]);
    expect(groups[0]!.clips.map((item) => item.id)).toEqual([3, 2]);
  });

  it("keeps only the clips each filter asks for", () => {
    const ids = (filter: "unplayed" | "played" | "missing") =>
      groupLibrary(library, "", filter).flatMap((group) => group.clips.map((item) => item.id));

    // A missing clip is not "unplayed": it cannot be played at all.
    expect(ids("unplayed")).toEqual([2, 1]);
    expect(ids("played")).toEqual([3]);
    expect(ids("missing")).toEqual([4]);
  });

  it("matches every search word against title, prova, team and car number", () => {
    const groups = groupLibrary(library, "enduro 42", "all");

    expect(groups.flatMap((group) => group.clips.map((item) => item.id))).toEqual([2]);
  });
});
