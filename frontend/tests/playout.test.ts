/**
 * Tests for the Playout page's pure helpers (src/lib/playout.ts): the
 * countdown colours, the keyboard map, the library grouping and the strip's
 * remaining times.
 */

import { describe, expect, it } from "vitest";

import {
  clipDetails,
  countdownColour,
  formatDuration,
  formatKnownDuration,
  formatPosition,
  formatRemaining,
  groupLibrary,
  playoutKeyAction,
  programProgress,
  remainingAfterOnAir,
  transportArgs,
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
    // L has no meaning without a clip on air: Replay last is a button only.
    expect(press("l")).toBeNull();
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

    expect(groups.map((group) => group.name)).toEqual(["Enduro", "Suspensão", "Clips"]);
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

describe("groups by kind", () => {
  // A delivered pass, a sponsor clip, and an old file in the receiver root
  // that the manifest does not know.
  const mixed = {
    "1": clip({ id: 1, source: "orchestrator", title: "Enduro · carro 42", prova: "Enduro" }),
    "2": clip({ id: 2, title: "Logo", folder_label: "Patrocinadores" }),
    "3": clip({ id: 3, title: "manobrabilidade-001-p2-v2", kind: "unmatched" }),
  };

  it("lists provas, then labelled folders, then Unmatched", () => {
    expect(groupLibrary(mixed, "", "all").map((group) => group.name)).toEqual([
      "Enduro",
      "Patrocinadores",
      "Unmatched",
    ]);
  });

  it("leaves the unmatched files out when asked", () => {
    expect(groupLibrary(mixed, "", "all", true).map((group) => group.name)).toEqual([
      "Enduro",
      "Patrocinadores",
    ]);
  });

  it("gives a second line only when the title does not already say it", () => {
    // The pipeline title already names prova and car; a labelled one does not.
    expect(clipDetails(mixed["1"])).toBeNull();
    expect(clipDetails({ ...mixed["1"], title: "Melhor passagem" })).toBe("Enduro");
    expect(clipDetails(mixed["2"])).toBe("Patrocinadores");
  });
});

describe("the strip's remaining times", () => {
  it("adds the cued clip and the queue, counting unknown durations apart", () => {
    const preview = { clip: clip({ duration_s: 30 }), slot: "b" as const };
    const queue = [{ duration_s: 45 }, { duration_s: null }];

    const after = remainingAfterOnAir(preview, queue);

    expect(after).toEqual({ seconds: 75, unknown: 1 });
    expect(formatRemaining(after.seconds, after.unknown)).toBe("−1:15 +?");
  });

  it("says +? when nothing after the clip has a known duration", () => {
    expect(formatRemaining(0, 2)).toBe("+?");
    expect(formatRemaining(0, 0)).toBe("−0:00");
  });

  it("is the share of the clip already played, for the strip's background", () => {
    const program = {
      clip: clip(),
      slot: "a" as const,
      started_at: "",
      position_s: 15,
      duration_s: 60,
      remaining_s: 45,
    };

    expect(programProgress(program)).toBe(0.25);
    expect(programProgress({ ...program, duration_s: null })).toBe(0);
    expect(programProgress(null)).toBe(0);
  });

  it("shows ? for a duration ffprobe could not measure", () => {
    expect(formatKnownDuration(null)).toBe("?");
    expect(formatKnownDuration(61)).toBe("1:01");
  });
});

describe("media-control keys (WP5c)", () => {
  const press = (key: string, transportEnabled: boolean) =>
    playoutKeyAction({ key, targetIsEditable: false, transportEnabled });

  it("K, J and L drive the clip on air whenever one is on air", () => {
    expect(["k", "J", "l"].map((key) => press(key, true))).toEqual([
      "transport_toggle",
      "transport_back",
      "transport_forward",
    ]);
    // With nothing on air they do nothing (L is not Replay last any more).
    expect(["k", "j", "L"].map((key) => press(key, false))).toEqual([null, null, null]);
  });

  it("has no frame-step keys: OBS can only jump to keyframes (LIVESTACK-56)", () => {
    expect([",", "."].map((key) => press(key, true))).toEqual([null, null]);
  });

  it("keeps the other shortcuts with a clip on air", () => {
    expect(press(" ", true)).toBe("take");
    expect(press("ArrowRight", true)).toBe("skip");
  });

  it("turns each key into the transport command the server expects", () => {
    expect(transportArgs("transport_toggle")).toEqual({ action: "toggle" });
    expect(transportArgs("transport_back")).toEqual({ action: "nudge", seconds: -5 });
    expect(transportArgs("transport_forward")).toEqual({ action: "nudge", seconds: 5 });
  });
});

describe("formatPosition", () => {
  it("shows a scrub position to a tenth of a second", () => {
    expect(formatPosition(4.24)).toBe("0:04.2");
    expect(formatPosition(61.96)).toBe("1:02.0");
    expect(formatPosition(-1)).toBe("0:00.0");
  });
});
