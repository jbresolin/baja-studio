/**
 * Tests for the audio pages' pure helpers (src/lib/audio.ts).
 */

import { describe, expect, it } from "vitest";

import {
  channelLabel,
  d200Urls,
  dbToLevel,
  formatDb,
  levelToDb,
  mirrorSteps,
  moveItem,
  newMacro,
  nextLinkState,
  pointLabel,
  type MacroConfig,
} from "../src/lib/audio";

describe("the XR18 fader law", () => {
  it("matches the mixer's scale at the segment corners, as on the server", () => {
    expect(levelToDb(0.75)).toBeCloseTo(0);
    expect(levelToDb(0.5)).toBeCloseTo(-10);
    expect(levelToDb(0.25)).toBeCloseTo(-30);
    expect(levelToDb(1)).toBeCloseTo(10);
    expect(dbToLevel(-60)).toBeCloseTo(0.0625);
  });

  it("formats like the server: -inf for off, a dash when unknown", () => {
    expect(formatDb(0.5)).toBe("-10.0 dB");
    expect(formatDb(0)).toBe("-inf");
    expect(formatDb(null)).toBe("—");
  });
});

describe("names", () => {
  const channels = { "1": { label: "HOST 1", color: "#000000", visible: true } };

  it("uses the label from the setup, or a plain name", () => {
    expect(channelLabel(channels, "1")).toBe("HOST 1");
    expect(channelLabel(channels, "bus3")).toBe("Bus 3");
    expect(pointLabel(channels, { channel: "1", bus: "bus2" })).toBe("HOST 1 → Bus 2");
    expect(pointLabel(channels, { channel: "1", bus: "main" })).toBe("HOST 1");
  });
});

describe("editing helpers", () => {
  it("cycles link states unlinked → linked → paused → unlinked", () => {
    expect(nextLinkState("unlinked")).toBe("linked");
    expect(nextLinkState("linked")).toBe("paused");
    expect(nextLinkState("paused")).toBe("unlinked");
  });

  it("gives a new macro an id not in use", () => {
    const existing = [{ id: "macro-2" }, { id: "macro-3" }] as MacroConfig[];

    expect(newMacro(existing).id).toBe("macro-4");
  });

  it("moves an item and ignores moves past the ends", () => {
    expect(moveItem(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
    expect(moveItem(["a", "b"], 0, -1)).toEqual(["a", "b"]);
  });

  it("builds off steps that undo the on steps", () => {
    const points = [{ channel: "5", bus: "bus1" }];

    expect(
      mirrorSteps([
        { op: "restore_levels", points },
        { op: "set_mute", channel: "2", muted: true },
        { op: "recall_scene", snapshot: 3 },
      ]),
    ).toEqual([
      { op: "store_levels", points, silence: true },
      { op: "set_mute", channel: "2", muted: false },
    ]);
  });

  it("lists the D200 URLs for this host", () => {
    const urls = d200Urls("http://studio:8888/", ["host1-talk-prod"]).map((entry) => entry.url);

    expect(urls).toContain("http://studio:8888/action/mute/1");
    expect(urls).toContain("http://studio:8888/action/macro-state/host1-talk-prod");
  });
});
