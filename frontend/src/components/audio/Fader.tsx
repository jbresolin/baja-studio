/**
 * A vertical fader for touch and mouse: drag anywhere on the track, or use
 * the arrow keys (1 dB a press, Page Up/Down 6 dB).
 *
 * While dragging, the fader shows the finger's position, not the server's,
 * and sends at most every 50 ms with `dragging: true` (kept out of the
 * command log); letting go sends the final value without it.
 */

import { useRef, useState } from "react";

import { dbToLevel, formatDb, levelToDb } from "../../lib/audio";

const SEND_EVERY_MS = 50;

export interface FaderProps {
  label: string;
  level: number | null;
  color?: string;
  disabled?: boolean;
  onChange: (level: number, dragging: boolean) => void;
}

export function Fader({ label, level, color, disabled, onChange }: FaderProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [dragLevel, setDragLevel] = useState<number | null>(null);
  const lastSent = useRef(0);
  const shown = dragLevel ?? level ?? 0;

  const levelAt = (clientY: number): number => {
    const rect = trackRef.current!.getBoundingClientRect();
    if (rect.height === 0) return shown;
    return Math.min(1, Math.max(0, (rect.bottom - clientY) / rect.height));
  };

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const next = levelAt(event.clientY);
    setDragLevel(next);
    lastSent.current = Date.now();
    onChange(next, true);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (dragLevel === null) return;
    const next = levelAt(event.clientY);
    setDragLevel(next);
    if (Date.now() - lastSent.current >= SEND_EVERY_MS) {
      lastSent.current = Date.now();
      onChange(next, true);
    }
  };

  const onPointerUp = () => {
    if (dragLevel === null) return;
    onChange(dragLevel, false);
    setDragLevel(null);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    const steps: Record<string, number> = { ArrowUp: 1, ArrowDown: -1, PageUp: 6, PageDown: -6 };
    const step = steps[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const current = level ?? 0;
    const startDb = current <= 0 ? -90 : levelToDb(current);
    onChange(dbToLevel(startDb + step), false);
  };

  return (
    <div
      className={disabled ? "fader disabled" : "fader"}
      ref={trackRef}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={level ?? undefined}
      aria-valuetext={formatDb(level)}
      aria-disabled={disabled}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
    >
      <div className="fader-unity" style={{ bottom: "75%" }} />
      <div
        className="fader-fill"
        style={{ height: `${shown * 100}%`, background: color ?? undefined }}
      />
      <div className="fader-thumb" style={{ bottom: `calc(${shown * 100}% - 10px)` }} />
    </div>
  );
}
