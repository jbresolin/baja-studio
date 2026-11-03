/**
 * Tests for the Cameras page's control maths and keyboard map (src/lib/webControls.ts).
 *
 * Plain functions: inputs in, velocity or key action out.
 */

import { describe, expect, it } from "vitest";

import {
  applyDeadband,
  combineControls,
  HeldKeys,
  keyAction,
  NO_INPUT,
  type KeyLike,
} from "../src/lib/webControls";

const OPTIONS = { deadband: 0.1, speed: 1, zoomVelocity: 0.6 };

function key(code: string, keyName: string, extra: Partial<KeyLike> = {}): KeyLike {
  return {
    code,
    key: keyName,
    shiftKey: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    repeat: false,
    ...extra,
  };
}

describe("applyDeadband", () => {
  it("treats a stick inside the deadband as at rest", () => {
    expect(applyDeadband(0.05, 0.1)).toBe(0);
    expect(applyDeadband(-0.1, 0.1)).toBe(0);
  });

  it("starts from zero just past the deadband and still reaches full speed", () => {
    // Like the server's shaping: what is outside the deadband is rescaled.
    expect(applyDeadband(0.55, 0.1)).toBeCloseTo(0.5);
    expect(applyDeadband(-1, 0.1)).toBe(-1);
  });
});

describe("combineControls", () => {
  it("applies the deadband to the on-screen stick, then the speed slider", () => {
    const velocity = combineControls(
      { ...NO_INPUT, stick: { pan: 0.55, tilt: 0.05 } },
      { ...OPTIONS, speed: 0.5 },
    );

    expect(velocity).toEqual({ pan: 0.25, tilt: 0, zoom: 0 });
  });

  it("gives full deflection for a key, scaled by the speed slider", () => {
    const velocity = combineControls(
      { ...NO_INPUT, keys: { pan: -1, tilt: 0 } },
      { ...OPTIONS, speed: 0.3 },
    );

    expect(velocity.pan).toBe(-0.3);
  });

  it("zooms at the joystick's fixed zoom speed, whatever the speed slider says", () => {
    const velocity = combineControls({ ...NO_INPUT, zoom: 1 }, { ...OPTIONS, speed: 0.2 });

    expect(velocity).toEqual({ pan: 0, tilt: 0, zoom: 0.6 });
  });

  it("is exactly at rest when nothing is held, so nothing is sent", () => {
    expect(combineControls(NO_INPUT, OPTIONS)).toEqual({ pan: 0, tilt: 0, zoom: 0 });
  });

  it("never goes past full speed when a key and the stick push the same way", () => {
    const velocity = combineControls(
      { ...NO_INPUT, keys: { pan: 1, tilt: 0 }, stick: { pan: 1, tilt: 0 } },
      OPTIONS,
    );

    expect(velocity.pan).toBe(1);
  });
});

describe("HeldKeys", () => {
  it("two arrow keys held at once give one diagonal", () => {
    const held = new HeldKeys();

    held.set("ArrowRight", true);
    held.set("ArrowUp", true);

    expect(held.keys()).toEqual({ pan: 1, tilt: 1 });
  });

  it("letting go of one of two keys keeps the other", () => {
    const held = new HeldKeys();
    held.set("ArrowLeft", true);
    held.set("ArrowDown", true);

    held.set("ArrowLeft", false);

    expect(held.keys()).toEqual({ pan: 0, tilt: -1 });
  });

  it("Z zooms in and X zooms out", () => {
    const held = new HeldKeys();

    held.set("KeyZ", true);
    expect(held.zoom()).toBe(1);
    held.set("KeyZ", false);
    held.set("KeyX", true);
    expect(held.zoom()).toBe(-1);
  });

  it("other keys are not motion keys", () => {
    expect(new HeldKeys().set("KeyA", true)).toBe(false);
  });
});

describe("keyAction", () => {
  it("1–9 recall presets 0–8 and Shift stores them", () => {
    expect(keyAction(key("Digit1", "1"))).toEqual({ kind: "recall", n: 0 });
    expect(keyAction(key("Digit9", "("))).toEqual({ kind: "recall", n: 8 });
    expect(keyAction(key("Digit3", "#", { shiftKey: true }))).toEqual({ kind: "store", n: 2 });
  });

  it("Alt+1–9 select cameras, and [ ] step through them", () => {
    expect(keyAction(key("Digit2", "2", { altKey: true }))).toEqual({ kind: "select", index: 1 });
    expect(keyAction(key("BracketRight", "]"))).toEqual({ kind: "step-camera", by: 1 });
    expect(keyAction(key("BracketLeft", "["))).toEqual({ kind: "step-camera", by: -1 });
  });

  it("+ and - change speed, Space stops, ? shows the legend", () => {
    expect(keyAction(key("Equal", "+", { shiftKey: true }))).toEqual({ kind: "speed", by: 0.1 });
    expect(keyAction(key("Minus", "-"))).toEqual({ kind: "speed", by: -0.1 });
    expect(keyAction(key("Space", " "))).toEqual({ kind: "stop" });
    expect(keyAction(key("Slash", "?", { shiftKey: true }))).toEqual({ kind: "legend" });
  });

  it("leaves browser shortcuts alone", () => {
    expect(keyAction(key("Digit1", "1", { ctrlKey: true }))).toBeNull();
  });
});
