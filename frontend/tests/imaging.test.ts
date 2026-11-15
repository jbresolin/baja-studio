/**
 * Tests for the image-settings helpers (src/lib/imaging.ts): labels, All
 * auto, and the throttle a dragged slider sends through.
 */

import { describe, expect, it } from "vitest";

import {
  allAutoChanges,
  controlLabel,
  createLatestSender,
  shortPath,
  usbDeviceLabel,
  type ImageControlView,
} from "../src/lib/imaging";

function control(auto: boolean | null): ImageControlView {
  return { value: 0, auto, min: 0, max: 10, step: 1, default: 0, error: "" };
}

/** A send whose replies the test gives by hand, in order. */
function manualSend() {
  const sent: number[] = [];
  const replies: (() => void)[] = [];
  return {
    sent,
    send: (value: number) => {
      sent.push(value);
      return new Promise<void>((resolve) => replies.push(resolve));
    },
    /** The server answers the oldest write still waiting. */
    async reply() {
      replies.shift()?.();
      await Promise.resolve();
      await Promise.resolve();
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

  it("a dragged slider keeps one write in flight and then sends only the latest", async () => {
    const server = manualSend();
    const sender = createLatestSender<number>(server.send);

    // A quick drag: ten positions before the first write is answered.
    for (let value = 1; value <= 10; value++) sender.push(value);
    expect(server.sent).toEqual([1]); // the first went at once; the rest wait

    await server.reply();
    // Only the latest position (10) goes next; the ones in between never do.
    expect(server.sent).toEqual([1, 10]);

    await server.reply();
    expect(server.sent).toEqual([1, 10]); // nothing more to send
  });

  it("a refused write still lets the next one go", async () => {
    const sent: number[] = [];
    const sender = createLatestSender<number>((value) => {
      sent.push(value);
      return Promise.reject(new Error("refused"));
    });

    sender.push(1);
    sender.push(2);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(sent).toEqual([1, 2]);
  });

  it("a cancelled sender (the slider went away) sends nothing more", async () => {
    const server = manualSend();
    const sender = createLatestSender<number>(server.send);
    sender.push(1);
    sender.push(2);

    sender.cancel();
    await server.reply();

    expect(server.sent).toEqual([1]);
  });
});
