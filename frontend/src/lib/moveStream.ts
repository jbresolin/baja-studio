/**
 * Hold-to-move: how the browser drives a camera (WP3 brief §2, ADR 0004).
 *
 * While any control is held (a key, a button, the on-screen stick), the page
 * sends `move` every 100 ms with a 300 ms ttl, so the server stops the camera
 * by itself if the page goes quiet (a lost key-up, a dead tab, Wi-Fi gone).
 * On release it sends one `stop` at once.
 *
 * - A `stop` is sent only when the operator lets go or presses Stop. Never
 *   on page load, focus, reconnect or camera switch: on an agent camera
 *   every stop pauses auto (contract §7).
 * - A release sends `stop` with `release: true`: the server applies it only
 *   if this page moves the camera, so letting go after a refused move does
 *   not stop whoever owns the camera. Stop (button or Space) is a plain stop,
 *   which the server always obeys.
 * - After a camera switch, a Stop or a tab going hidden while held, the
 *   stream stays quiet until every control is let go: a key still held must
 *   not start moving the newly selected camera.
 */

import type { Velocity } from "./cameras";

export const MOVE_INTERVAL_MS = 100;
export const MOVE_TTL_MS = 300;

export type SendCommand = (command: string, args: Record<string, unknown>) => Promise<unknown>;

export const AT_REST: Velocity = { pan: 0, tilt: 0, zoom: 0 };

export function isAtRest(velocity: Velocity): boolean {
  return velocity.pan === 0 && velocity.tilt === 0 && velocity.zoom === 0;
}

function sameVelocity(a: Velocity, b: Velocity): boolean {
  return a.pan === b.pan && a.tilt === b.tilt && a.zoom === b.zoom;
}

export interface MoveStreamOptions {
  /** Sends a cameras-module command; resolves with the reply or rejects with its error. */
  send: SendCommand;
  /** Told the server's message when a move is refused (another source owns the camera). */
  onRefused?: (message: string) => void;
  intervalMs?: number;
  ttlMs?: number;
}

export class MoveStream {
  private readonly send: SendCommand;
  private readonly onRefused: (message: string) => void;
  private readonly intervalMs: number;
  private readonly ttlMs: number;
  private camera: string | null = null;
  private velocity: Velocity = AT_REST;
  private holding = false;
  // Set after a camera switch, Stop or hidden tab while held: quiet until all is let go.
  private waitingForRelease = false;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(options: MoveStreamOptions) {
    this.send = options.send;
    this.onRefused = options.onRefused ?? (() => undefined);
    this.intervalMs = options.intervalMs ?? MOVE_INTERVAL_MS;
    this.ttlMs = options.ttlMs ?? MOVE_TTL_MS;
  }

  /** True while a move stream is running. */
  isHolding(): boolean {
    return this.holding;
  }

  /**
   * Select the camera the controls drive. While held, the previous camera
   * gets a release stop first, and nothing goes to the new one until the
   * controls are let go.
   */
  setCamera(camera: string | null): void {
    if (camera === this.camera) return;
    if (this.holding) this.release();
    this.camera = camera;
  }

  /** The combined, shaped velocity of every control; at rest means "all let go". */
  update(velocity: Velocity): void {
    if (isAtRest(velocity)) {
      this.waitingForRelease = false;
      if (this.holding) this.release();
      this.velocity = AT_REST;
      return;
    }
    if (this.waitingForRelease || this.camera === null) return;
    const changed = !this.holding || !sameVelocity(velocity, this.velocity);
    this.velocity = velocity;
    if (!this.holding) {
      this.holding = true;
      this.timer = setInterval(() => this.sendMove(), this.intervalMs);
    }
    if (changed) this.sendMove();
  }

  /** The Stop button or Space: a plain stop, always sent and always obeyed. */
  stop(): void {
    this.endHold();
    if (this.camera !== null) {
      void this.send("stop", { camera: this.camera }).catch(() => undefined);
    }
  }

  /** The page cannot see the controls any more (tab hidden, window blurred, unmount). */
  release(): void {
    if (!this.holding) return;
    const camera = this.camera;
    this.endHold();
    if (camera !== null) {
      void this.send("stop", { camera, release: true }).catch(() => undefined);
    }
  }

  private endHold(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    if (this.holding || !isAtRest(this.velocity)) this.waitingForRelease = true;
    this.holding = false;
    this.velocity = AT_REST;
  }

  private sendMove(): void {
    if (!this.holding || this.camera === null) return;
    const { pan, tilt, zoom } = this.velocity;
    this.send("move", { camera: this.camera, pan, tilt, zoom, ttl_ms: this.ttlMs }).catch(
      (error: Error) => this.onRefused(error.message),
    );
  }
}
