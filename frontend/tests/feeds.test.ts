/**
 * Tests for the Feeds page's formatting helpers (src/lib/feeds.ts).
 *
 * They turn the server's numbers into what an operator reads on a card.
 */

import { describe, expect, it } from "vitest";

import {
  describePicture,
  FEED_NAME_PATTERN,
  formatBitrate,
  formatFreeSpace,
  newFeedId,
  type FeedLive,
} from "../src/lib/feeds";

const LIVE: FeedLive = {
  status: "live",
  since: null,
  protocol: "SRT",
  video_codec: "H264",
  width: 1920,
  height: 1080,
  audio_codec: "MPEG-4 Audio",
  bitrate_kbps: 6000,
  readers: 1,
};

describe("feeds helpers", () => {
  it("shows the bitrate in kbit/s below 1 Mbit/s and in Mbit/s above", () => {
    expect(formatBitrate(850)).toBe("850 kbit/s");
    expect(formatBitrate(4200)).toBe("4.2 Mbit/s");
    // The first poll of a sender has no rate yet.
    expect(formatBitrate(null)).toBe("—");
  });

  it("describes the picture and sound that arrive", () => {
    expect(describePicture(LIVE)).toBe("1920×1080 H264 + MPEG-4 Audio");
    // A drone app that sends no sound.
    expect(describePicture({ ...LIVE, audio_codec: null })).toBe("1920×1080 H264");
    expect(describePicture({ ...LIVE, width: null, height: null, video_codec: null })).toBe(
      "MPEG-4 Audio",
    );
  });

  it("shows free disk space in GB", () => {
    expect(formatFreeSpace(50 * 1024 ** 3)).toBe("50.0 GB free");
    expect(formatFreeSpace(null)).toBe("");
  });

  it("accepts the same feed names as the server", () => {
    expect(FEED_NAME_PATTERN.test("laptop1")).toBe(true);
    expect(FEED_NAME_PATTERN.test("cam-2_b")).toBe(true);
    expect(FEED_NAME_PATTERN.test("Drone")).toBe(false);
    expect(FEED_NAME_PATTERN.test("drone 2")).toBe(false);
    expect(FEED_NAME_PATTERN.test("")).toBe(false);
  });

  it("gives every new feed its own id, shaped like the server's", () => {
    const first = newFeedId();
    expect(first).toMatch(/^feed-[0-9a-f]{8}$/);
    expect(newFeedId()).not.toBe(first);
  });
});
