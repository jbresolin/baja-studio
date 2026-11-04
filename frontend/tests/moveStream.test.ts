/**
 * Tests for hold-to-move (src/lib/moveStream.ts).
 *
 * `MoveStream` is given a fake `send` that just writes down each command,
 * and Vitest's fake timers stand in for time, so "100 ms later" is
 * `vi.advanceTimersByTime(100)` and nothing really waits.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MoveStream } from "../src/lib/moveStream";

interface Sent {
  command: string;
  args: Record<string, unknown>;
}

/** A stream on camera "smtav" whose commands land in `sent`; `refuse` makes moves fail. */
function streamWithLog(options: { refuse?: string } = {}) {
  const sent: Sent[] = [];
  const refusals: string[] = [];
  const stream = new MoveStream({
    send: (command, args) => {
      sent.push({ command, args });
      return options.refuse && command === "move"
        ? Promise.reject(new Error(options.refuse))
        : Promise.resolve({});
    },
    onRefused: (message) => refusals.push(message),
  });
  stream.setCamera("smtav");
  return { stream, sent, refusals };
}

const RIGHT = { pan: 0.5, tilt: 0, zoom: 0 };
const AT_REST = { pan: 0, tilt: 0, zoom: 0 };

describe("MoveStream", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends a move at once and again every 100 ms while held, each with a 300 ms ttl", () => {
    const { stream, sent } = streamWithLog();

    stream.update(RIGHT);
    vi.advanceTimersByTime(350);

    // One at the press, then at 100, 200 and 300 ms.
    expect(sent).toHaveLength(4);
    expect(sent.every((item) => item.command === "move")).toBe(true);
    expect(sent[0]!.args).toEqual({ camera: "smtav", pan: 0.5, tilt: 0, zoom: 0, ttl_ms: 300 });
  });

  it("sends one release stop when let go, and nothing after", () => {
    const { stream, sent } = streamWithLog();
    stream.update(RIGHT);

    stream.update(AT_REST);
    vi.advanceTimersByTime(1000);

    expect(sent.map((item) => item.command)).toEqual(["move", "stop"]);
    // A release applies only to a camera this page moves (ADR 0004).
    expect(sent[1]!.args).toEqual({ camera: "smtav", release: true });
  });

  it("sends nothing at all while nothing is held", () => {
    const { stream, sent } = streamWithLog();

    stream.update(AT_REST);
    stream.update(AT_REST);
    vi.advanceTimersByTime(1000);

    expect(sent).toEqual([]);
  });

  it("sends a changed velocity at once, without waiting for the next 100 ms", () => {
    const { stream, sent } = streamWithLog();
    stream.update(RIGHT);

    vi.advanceTimersByTime(30);
    stream.update({ pan: 0.5, tilt: 0.5, zoom: 0 });

    expect(sent).toHaveLength(2);
    expect(sent[1]!.args).toMatchObject({ pan: 0.5, tilt: 0.5 });
  });

  it("does not resend when the same velocity is reported again", () => {
    const { stream, sent } = streamWithLog();

    stream.update(RIGHT);
    stream.update(RIGHT);
    stream.update(RIGHT);

    expect(sent).toHaveLength(1);
  });

  it("switching camera while held stops the old camera and waits for a fresh press", () => {
    const { stream, sent } = streamWithLog();
    stream.update(RIGHT);

    stream.setCamera("wide");
    stream.update(RIGHT); // the key is still down: the new camera must not start moving
    vi.advanceTimersByTime(500);

    expect(sent.map((item) => [item.command, item.args.camera])).toEqual([
      ["move", "smtav"],
      ["stop", "smtav"],
    ]);

    // Let go and press again: now the new camera moves.
    stream.update(AT_REST);
    stream.update(RIGHT);
    expect(sent.at(-1)).toMatchObject({ command: "move", args: { camera: "wide" } });
  });

  it("switching camera while idle sends nothing", () => {
    const { stream, sent } = streamWithLog();

    stream.setCamera("wide");
    stream.setCamera("smtav");

    expect(sent).toEqual([]);
  });

  it("Stop sends a plain stop, which the server always obeys, even while idle", () => {
    const { stream, sent } = streamWithLog();
    stream.update(RIGHT);

    stream.stop();
    vi.advanceTimersByTime(500);
    stream.stop();

    expect(sent.map((item) => [item.command, item.args])).toEqual([
      ["move", { camera: "smtav", pan: 0.5, tilt: 0, zoom: 0, ttl_ms: 300 }],
      ["stop", { camera: "smtav" }],
      ["stop", { camera: "smtav" }],
    ]);
  });

  it("release (tab hidden) stops a held camera once and ignores the still-held control", () => {
    const { stream, sent } = streamWithLog();
    stream.update(RIGHT);

    stream.release();
    stream.release();
    stream.update(RIGHT);
    vi.advanceTimersByTime(500);

    expect(sent.map((item) => item.command)).toEqual(["move", "stop"]);
  });

  it("reports each refusal so the page can show it, and keeps trying", async () => {
    const { stream, sent, refusals } = streamWithLog({
      refuse: "camera smtav is being moved by joystick:desk; it takes over once that stops",
    });

    stream.update(RIGHT);
    // The async version also lets the refused replies (promises) settle.
    await vi.advanceTimersByTimeAsync(100);

    expect(refusals).toHaveLength(2);

    expect(refusals[0]).toMatch(/joystick:desk/);
    expect(sent.filter((item) => item.command === "move")).toHaveLength(2);
  });

  it("with no camera selected nothing is sent", () => {
    const { stream, sent } = streamWithLog();
    stream.setCamera(null);

    stream.update(RIGHT);
    stream.update(AT_REST);

    expect(sent).toEqual([]);
  });
});
