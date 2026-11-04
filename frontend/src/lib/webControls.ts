/**
 * The Cameras page's controls turned into one velocity, and its keyboard map.
 *
 * Every control adds up: arrow keys, the on-screen stick and the zoom
 * buttons/keys. The stick gets the joystick deadband (the same setting as the
 * real joystick), then pan and tilt are scaled by the speed slider. Keys give
 * full deflection. Zoom goes at the joystick's fixed zoom speed.
 *
 * Keys (as in ptz_test.py): arrows pan/tilt, Z/X zoom in/out, 1–9 recall
 * preset, Shift+1–9 store it, +/- speed, Space stop, ? legend. Cameras: [ and
 * ] step through the list, Alt+1–9 pick one (plain digits are presets).
 */

import type { Velocity } from "./cameras";

export const SPEED_MIN = 0.1;
export const SPEED_MAX = 1.0;
export const SPEED_STEP = 0.1;
export const DEFAULT_ZOOM_VELOCITY = 0.6;

/** A stick axis with the deadband taken out and the rest rescaled to start from 0. */
export function applyDeadband(value: number, deadband: number): number {
  const size = Math.abs(value);
  if (size <= deadband || deadband >= 1) return 0;
  return (Math.sign(value) * (Math.min(size, 1) - deadband)) / (1 - deadband);
}

export interface ControlInputs {
  /** Arrow keys: -1, 0 or 1 per axis. */
  keys: { pan: number; tilt: number };
  /** The on-screen stick, -1..1 per axis, before the deadband. */
  stick: { pan: number; tilt: number };
  /** Zoom buttons or keys: -1 (out), 0 or 1 (in). */
  zoom: number;
}

export const NO_INPUT: ControlInputs = {
  keys: { pan: 0, tilt: 0 },
  stick: { pan: 0, tilt: 0 },
  zoom: 0,
};

function clamp(value: number): number {
  return Math.max(-1, Math.min(1, value));
}

function round(value: number): number {
  // Three decimals: enough for a camera, and it keeps tiny float noise from
  // looking like a new velocity (which is sent at once).
  return Math.round(value * 1000) / 1000 || 0;
}

/** The velocity sent to the server for these inputs. */
export function combineControls(
  inputs: ControlInputs,
  options: { deadband: number; speed: number; zoomVelocity: number },
): Velocity {
  const pan = inputs.keys.pan + applyDeadband(inputs.stick.pan, options.deadband);
  const tilt = inputs.keys.tilt + applyDeadband(inputs.stick.tilt, options.deadband);
  return {
    pan: round(clamp(pan) * options.speed),
    tilt: round(clamp(tilt) * options.speed),
    zoom: round(clamp(inputs.zoom) * options.zoomVelocity),
  };
}

export function clampSpeed(speed: number): number {
  return Math.round(Math.max(SPEED_MIN, Math.min(SPEED_MAX, speed)) * 10) / 10;
}

// --- the keyboard ------------------------------------------------------------------

/** What a key press asks for, besides the held-key motion handled by `HeldKeys`. */
export type KeyAction =
  | { kind: "recall"; n: number }
  | { kind: "store"; n: number }
  | { kind: "select"; index: number }
  | { kind: "step-camera"; by: number }
  | { kind: "speed"; by: number }
  | { kind: "stop" }
  | { kind: "legend" };

const MOTION_KEYS: Record<string, keyof HeldKeysState> = {
  ArrowLeft: "left",
  ArrowRight: "right",
  ArrowUp: "up",
  ArrowDown: "down",
  KeyZ: "zoomIn",
  KeyX: "zoomOut",
};

interface HeldKeysState {
  left: boolean;
  right: boolean;
  up: boolean;
  down: boolean;
  zoomIn: boolean;
  zoomOut: boolean;
}

export interface KeyLike {
  code: string;
  key: string;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  repeat: boolean;
}

/** Which motion keys are held. Auto-repeat events change nothing. */
export class HeldKeys {
  private held: HeldKeysState = {
    left: false,
    right: false,
    up: false,
    down: false,
    zoomIn: false,
    zoomOut: false,
  };

  /** A key went down or up; returns whether it was a motion key. */
  set(code: string, down: boolean): boolean {
    const name = MOTION_KEYS[code];
    if (name === undefined) return false;
    this.held = { ...this.held, [name]: down };
    return true;
  }

  /** Forget every key (the window lost focus, so key-ups will not arrive). */
  clear(): void {
    this.held = {
      left: false,
      right: false,
      up: false,
      down: false,
      zoomIn: false,
      zoomOut: false,
    };
  }

  keys(): { pan: number; tilt: number } {
    const { left, right, up, down } = this.held;
    return { pan: Number(right) - Number(left), tilt: Number(up) - Number(down) };
  }

  zoom(): number {
    return Number(this.held.zoomIn) - Number(this.held.zoomOut);
  }
}

/** The action a key press asks for, if any (motion keys are `HeldKeys`' business). */
export function keyAction(event: KeyLike): KeyAction | null {
  if (event.ctrlKey || event.metaKey) return null; // browser shortcuts stay the browser's
  const digit = /^(?:Digit|Numpad)([1-9])$/.exec(event.code);
  if (digit) {
    const number = Number(digit[1]);
    if (event.altKey) return { kind: "select", index: number - 1 };
    return event.shiftKey ? { kind: "store", n: number - 1 } : { kind: "recall", n: number - 1 };
  }
  if (event.altKey) return null;
  switch (event.key) {
    case "+":
    case "=":
      return { kind: "speed", by: SPEED_STEP };
    case "-":
    case "_":
      return { kind: "speed", by: -SPEED_STEP };
    case " ":
      return { kind: "stop" };
    case "?":
      return { kind: "legend" };
    case "[":
      return { kind: "step-camera", by: -1 };
    case "]":
      return { kind: "step-camera", by: 1 };
  }
  return null;
}

const TEXT_INPUT_TYPES = new Set(["text", "number", "search", "email", "password", "url", "tel"]);

/**
 * True when the key press belongs to a text field, not to the camera
 * controls. The speed slider and buttons are not text fields: arrows still
 * drive the camera when they have focus.
 */
export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target instanceof HTMLInputElement) return TEXT_INPUT_TYPES.has(target.type);
  return (
    target.isContentEditable ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

export const KEY_LEGEND: [string, string][] = [
  ["← → ↑ ↓", "pan and tilt (two at once: diagonal)"],
  ["Z / X", "zoom in / out"],
  ["1–9", "recall preset"],
  ["Shift + 1–9", "store preset"],
  ["+ / −", "speed"],
  ["Space", "stop"],
  ["[ / ]", "previous / next camera"],
  ["Alt + 1–9", "select camera"],
  ["?", "show or hide this legend"],
];
