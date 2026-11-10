/**
 * Media controls for the clip on air (WP5c §3–4), under the program strip.
 *
 * They act on air, so they are locked until the operator holds "Unlock"
 * for two seconds; unlocked, the bar is marked LIVE in red and a Lock
 * button (or the clip leaving the air) locks it again. Unlocked, it offers
 * play/pause, restart, −5 s / +5 s, one frame back/forward (while paused),
 * a scrub bar that shows where it will go while dragged and seeks on
 * release, and the live replay speed (100 / 50 / 25 %).
 *
 * Every control is a `transport` or `set_program_speed` command; the
 * server acts on the program source only and never on the cued clip.
 */

import { useEffect, useRef, useState } from "react";

import {
  formatPosition,
  HOLD_TO_ENABLE_MS,
  NUDGE_SECONDS,
  REPLAY_SPEEDS,
  type ProgramView,
} from "../../lib/playout";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface MediaControlsProps {
  program: ProgramView;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  run: Run;
}

export function MediaControls({ program, enabled, onEnabledChange, run }: MediaControlsProps) {
  if (!enabled) return <HoldToUnlock onUnlocked={() => onEnabledChange(true)} />;
  const transport = program.transport;
  const paused = transport?.state === "paused";
  const speed = program.speed_percent ?? 100;
  const send = (args: Record<string, unknown>) => void run("transport", args);
  return (
    <div className="media-controls live" role="group" aria-label="Media controls (live)">
      <span className="live-tag">LIVE</span>
      <button
        aria-label={paused ? "Play" : "Pause"}
        title="Pause / play (K)"
        onClick={() => send({ action: paused ? "play" : "pause" })}
      >
        {paused ? "▶" : "❚❚"}
      </button>
      <button
        aria-label="Restart"
        title="Play from the top"
        onClick={() => send({ action: "restart" })}
      >
        ⏮
      </button>
      <button
        aria-label={`Back ${NUDGE_SECONDS} s`}
        title="J"
        onClick={() => send({ action: "nudge", seconds: -NUDGE_SECONDS })}
      >
        −{NUDGE_SECONDS} s
      </button>
      <button
        aria-label="One frame back"
        title={paused ? ", (comma)" : "Pause first"}
        disabled={!paused}
        onClick={() => send({ action: "step", frames: -1 })}
      >
        ◀|
      </button>
      <button
        aria-label="One frame forward"
        title={paused ? ". (period)" : "Pause first"}
        disabled={!paused}
        onClick={() => send({ action: "step", frames: 1 })}
      >
        |▶
      </button>
      <button
        aria-label={`Forward ${NUDGE_SECONDS} s`}
        title="L"
        onClick={() => send({ action: "nudge", seconds: NUDGE_SECONDS })}
      >
        +{NUDGE_SECONDS} s
      </button>
      <ScrubBar program={program} onSeek={(position_s) => send({ action: "seek", position_s })} />
      <span className="speed-buttons" role="radiogroup" aria-label="Speed on air">
        {REPLAY_SPEEDS.map((value) => (
          <button
            key={value}
            role="radio"
            aria-checked={speed === value}
            className={speed === value ? "active" : ""}
            onClick={() => {
              if (speed !== value) void run("set_program_speed", { speed_percent: value });
            }}
          >
            {value} %
          </button>
        ))}
      </span>
      <button className="small" onClick={() => onEnabledChange(false)}>
        Lock
      </button>
    </div>
  );
}

/**
 * The scrub bar: dragging only moves the preview of where it will go
 * (shown beside it); the seek is sent once, on release.
 */
function ScrubBar({
  program,
  onSeek,
}: {
  program: ProgramView;
  onSeek: (positionSeconds: number) => void;
}) {
  const [dragging, setDragging] = useState<number | null>(null);
  const duration = program.duration_s ?? 0;
  const fps = program.transport?.fps ?? 30;
  const position = dragging ?? program.position_s;
  const release = () => {
    if (dragging === null) return;
    onSeek(dragging);
    setDragging(null);
  };
  return (
    <span className="scrub">
      <input
        type="range"
        aria-label="Scrub"
        min={0}
        max={duration}
        step={1 / fps}
        disabled={duration <= 0}
        value={Math.min(position, duration)}
        onChange={(event) => setDragging(Number(event.target.value))}
        onPointerUp={release}
        onMouseUp={release}
        onTouchEnd={release}
        onKeyUp={release}
        onBlur={release}
      />
      <span className="scrub-position" data-testid="scrub-position">
        {dragging !== null && "→ "}
        {formatPosition(position)}
      </span>
    </span>
  );
}

/** "Hold to unlock": press and hold two seconds (mouse, touch or Enter); letting go earlier does nothing. */
function HoldToUnlock({ onUnlocked }: { onUnlocked: () => void }) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [holding, setHolding] = useState(false);
  const cancel = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    setHolding(false);
  };
  const start = () => {
    cancel();
    setHolding(true);
    timer.current = setTimeout(() => {
      timer.current = null;
      setHolding(false);
      onUnlocked();
    }, HOLD_TO_ENABLE_MS);
  };
  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );
  return (
    <div className="media-controls locked">
      <button
        className={holding ? "hold-to-unlock holding" : "hold-to-unlock"}
        style={{ "--hold-ms": `${HOLD_TO_ENABLE_MS}ms` } as React.CSSProperties}
        onPointerDown={start}
        onPointerUp={cancel}
        onPointerLeave={cancel}
        onKeyDown={(event) => {
          // Enter only: Space anywhere on the page is Take.
          if (event.key === "Enter" && !event.repeat) {
            event.preventDefault();
            start();
          }
        }}
        onKeyUp={cancel}
      >
        {holding ? "Keep holding…" : "Hold to unlock media controls"}
      </button>
      <span className="muted">They act on the clip on air.</span>
    </div>
  );
}
