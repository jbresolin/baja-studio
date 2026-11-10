/**
 * The personal-mix overlay on the hosts screen (WP9b §3): a seat's in-ears,
 * one row per source of its bus, opened by tapping the audio card.
 *
 * A source that follows the main mix shows the main level and the seat's
 * offset; + and − move the offset (the link stays), "reset to main" zeroes
 * them all. A source not linked to the bus is "independent": its own send
 * level, and + and − move that. Holding a button repeats (first after
 * `HOLD_FIRST_REPEAT_MS`, then every `HOLD_REPEAT_MS`), slow enough that the
 * mixer's packet budget is never at risk. The server checks every command
 * against the seat, so a screen can only ever change its own in-ears.
 *
 * It closes after `OVERLAY_IDLE_CLOSE_MS` without a touch; it does not cover
 * the audio card, which keeps updating underneath. Mixer offline or
 * production's "view only" disable the buttons and say why. Touch targets
 * are at least 48 px; it works with a mouse.
 */

import { useEffect, useRef } from "react";

import {
  formatOffset,
  followsMain,
  HOLD_FIRST_REPEAT_MS,
  HOLD_REPEAT_MS,
  OVERLAY_IDLE_CLOSE_MS,
  type MixRow,
  type SeatAudioView,
} from "../../lib/seatAudio";

type Run = (command: string, args: Record<string, unknown>) => Promise<unknown>;

interface SeatMixOverlayProps {
  seatId: number;
  seatName: string;
  color: string;
  view: SeatAudioView;
  run: Run;
  onClose: () => void;
}

export function SeatMixOverlay({
  seatId,
  seatName,
  color,
  view,
  run,
  onClose,
}: SeatMixOverlayProps) {
  const idle = useIdleClose(onClose);
  const rows = view.mixes[String(seatId)] ?? [];
  const seat = view.seats[String(seatId)];
  const disabledWhy = !view.mixer_online
    ? "Mixer offline"
    : !view.can_adjust
      ? "View only: production set the mixes"
      : null;
  const step = (row: MixRow, sign: 1 | -1) => {
    idle.touched();
    void run("set_seat_offset", {
      seat_id: seatId,
      channel: row.channel,
      delta_db: sign * view.step_db,
    });
  };
  return (
    <div
      className="hosts-mix-overlay"
      role="dialog"
      aria-label={`${seatName}'s in-ear mix`}
      style={{ borderColor: color }}
      onPointerDown={idle.touched}
    >
      <div className="hosts-mix-head">
        <h2 style={{ color }}>
          {seatName} · {seat?.iem_label ?? "in-ears"}
        </h2>
        <button className="hosts-mix-close" aria-label="Close" onClick={onClose}>
          ✕
        </button>
      </div>
      {disabledWhy && (
        <div className="hosts-mix-note" role="status">
          {disabledWhy}
        </div>
      )}
      <ul className="hosts-mix-rows">
        {rows.map((row) => (
          <li key={row.channel} className="hosts-mix-row" data-testid={`mix-row-${row.channel}`}>
            <span className="hosts-mix-label">{row.label}</span>
            <span className="hosts-mix-level">
              {followsMain(row) ? (
                <>
                  <span className="hosts-subtle">main {row.main_db ?? "–"}</span>{" "}
                  <strong data-testid={`mix-offset-${row.channel}`}>
                    {formatOffset(row.offset_db ?? 0)}
                  </strong>
                </>
              ) : (
                <>
                  <span className="hosts-subtle">independent</span>{" "}
                  <strong>{row.send_db ?? "–"}</strong>
                </>
              )}
            </span>
            <HoldButton
              label={`${row.label} down`}
              disabled={disabledWhy !== null}
              onStep={() => step(row, -1)}
            >
              −
            </HoldButton>
            <HoldButton
              label={`${row.label} up`}
              disabled={disabledWhy !== null}
              onStep={() => step(row, 1)}
            >
              +
            </HoldButton>
          </li>
        ))}
      </ul>
      <button
        className="hosts-mix-reset"
        disabled={disabledWhy !== null}
        onClick={() => {
          idle.touched();
          void run("reset_seat_offsets", { seat_id: seatId });
        }}
      >
        Reset to main
      </button>
    </div>
  );
}

interface HoldButtonProps {
  label: string;
  disabled: boolean;
  onStep: () => void;
  children: string;
}

/** A + or − that steps once on press and repeats while held (pointer or touch). */
function HoldButton({ label, disabled, onStep, children }: HoldButtonProps) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stop = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  };
  const repeat = (delay: number) => {
    timer.current = setTimeout(() => {
      onStep();
      repeat(HOLD_REPEAT_MS);
    }, delay);
  };
  useEffect(() => stop, []);
  return (
    <button
      className="hosts-mix-step"
      aria-label={label}
      disabled={disabled}
      onPointerDown={(event) => {
        event.preventDefault();
        stop();
        onStep();
        repeat(HOLD_FIRST_REPEAT_MS);
      }}
      onPointerUp={stop}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onKeyDown={(event) => {
        // Keyboard users step once per press of Enter or Space.
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onStep();
        }
      }}
    >
      {children}
    </button>
  );
}

/** Calls `onClose` after `OVERLAY_IDLE_CLOSE_MS` without `touched()`. */
function useIdleClose(onClose: () => void): { touched: () => void } {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });
  const touched = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => close.current(), OVERLAY_IDLE_CLOSE_MS);
  };
  // Started once when the overlay opens; touches restart it.
  useEffect(() => {
    timer.current = setTimeout(() => close.current(), OVERLAY_IDLE_CLOSE_MS);
    return () => {
      if (timer.current !== null) clearTimeout(timer.current);
    };
  }, []);
  return { touched };
}
