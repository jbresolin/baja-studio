/**
 * The status line on the audio pages: XR18, M-VAVE and D200 at a glance,
 * each with the reason when something is wrong.
 */

import { useEffect, useState } from "react";

import type { AudioState } from "../../lib/audio";

const D200_RECENT_MS = 10_000;
const CLOCK_TICK_MS = 2000;

/** The current time, updated every couple of seconds (for "seen recently"). */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function badgeClass(state: "ok" | "degraded" | "offline" | "stopped"): string {
  return `badge badge-${state}`;
}

export function AudioStatus({ state, now }: { state: AudioState; now: number }) {
  const { mixer, midi, d200 } = state;
  const mixerBadge =
    mixer.connection === "ok"
      ? "ok"
      : mixer.connection === "disabled"
        ? "stopped"
        : mixer.connection === "connecting"
          ? "degraded"
          : "offline";
  const midiBadge =
    midi.status === "ok" ? "ok" : midi.status === "disabled" ? "stopped" : "offline";
  const d200Seen = d200.last_seen !== null && now - Date.parse(d200.last_seen) < D200_RECENT_MS;
  const pickups = Object.entries(midi.pickup ?? {});

  return (
    <div className="audio-status" data-testid="audio-status">
      <span>
        <span className={badgeClass(mixerBadge)}>XR18</span>{" "}
        {mixer.connection === "ok" ? (
          <span className="muted">
            {mixer.name} · {mixer.packets_per_s.sent}↑ {mixer.packets_per_s.received}↓ pkt/s
          </span>
        ) : (
          <span className="status-message">{mixer.message || mixer.connection}</span>
        )}
      </span>
      <span>
        <span className={badgeClass(midiBadge)}>M-VAVE</span>{" "}
        <span className={midi.status === "ok" ? "muted" : "status-message"}>
          {midi.status === "ok" ? midi.device : midi.message || midi.status}
        </span>
      </span>
      <span>
        <span className={badgeClass(d200Seen ? "ok" : "stopped")}>D200</span>{" "}
        <span className="muted">{d200Seen ? "polling" : "not heard from recently"}</span>
      </span>
      {pickups.map(([strip, level]) => (
        <span key={strip} className="status-message">
          M-VAVE strip {strip}: move the fader to the mixer's level to take over
          <span className="muted"> ({Math.round(level * 100)}%)</span>
        </span>
      ))}
    </div>
  );
}
