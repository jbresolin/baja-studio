/**
 * Tests for the image-settings helpers (src/lib/imaging.ts): labels, All
 * auto, and the throttle a dragged slider sends through.
 */

import { describe, expect, it } from "vitest";

import {
  allAutoChanges,
  controlLabel,
  createThrottle,
  shortPath,
  usbDeviceLabel,
  type ImageControlView,
} from "../src/lib/imaging";

function control(auto: boolean | null): ImageControlView {
  return { value: 0, auto, min: 0, max: 10, step: 1, default: 0, error: "" };
}

/** A pretend clock and timer list, so the throttle runs without real time. */
function fakeTime() {
  let now = 0;
  const timers: { at: number; callback: () => void }[] = [];
  return {
    now: () => now,
    schedule: (callback: () => void, ms: number) => {
      const timer = { at: now + ms, callback };
      timers.push(timer);
      return () => timers.splice(timers.indexOf(timer), 1);
    },
    /** Let `ms` pass, running the timers that come due. */
    pass(ms: number) {
      now += ms;
      for (const timer of timers.filter((t) => t.at <= now)) {
        timers.splice(timers.indexOf(timer), 1);
        timer.callback();
      }
    },
  };
}

describe("image helpers", () => {
  it("names controls the way operators say them", () => {
    expect(controlLabel("white_balance")).toBe("White balance");
    expect(controlLabel("something_new")).toBe("something new");
  });

  it("All auto touches only controls that have an automatic mode", () => {
    expect(allAutoChanges({ exposure: control(false), gain: control(null) })).toEqual({
      exposure: { auto: true },
    });
  });

  it("numbers devices only when two share a name, and shows the USB port part of a path", () => {
    expect(usbDeviceLabel({ name: "EMEET SmartCam S600", index: 1 }, 2)).toBe(
      "EMEET SmartCam S600 #2",
    );
    expect(usbDeviceLabel({ name: "EMEET PIXY", index: 0 }, 1)).toBe("EMEET PIXY");
    expect(shortPath("\\\\?\\usb#vid_328f&pid_00c0&mi_00#7&1a2b3c4d&0&0000#{65e8}\\global")).toBe(
      "7&1a2b3c4d&0&0000",
    );
  });

  it("a dragged slider sends its first value at once, then at most every interval, and always the last", () => {
    const time = fakeTime();
    const sent: number[] = [];
    const throttle = createThrottle<number>(
      (value) => sent.push(value),
      150,
      time.now,
      time.schedule,
    );

    // A quick drag: ten positions within 90 ms.
    for (let value = 1; value <= 10; value++) {
      throttle.push(value);
      time.pass(10);
    }
    expect(sent).toEqual([1]); // the first went at once; the rest wait

    time.pass(100);
    // At 150 ms the latest position (10) goes; the ones in between never do.
    expect(sent).toEqual([1, 10]);

    time.pass(1000);
    expect(sent).toEqual([1, 10]); // nothing more to send
  });

  it("a cancelled throttle (the slider went away) sends nothing more", () => {
    const time = fakeTime();
    const sent: number[] = [];
    const throttle = createThrottle<number>(
      (value) => sent.push(value),
      150,
      time.now,
      time.schedule,
    );
    throttle.push(1);
    throttle.push(2);

    throttle.cancel();
    time.pass(500);

    expect(sent).toEqual([1]);
  });
});
