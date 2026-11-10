/**
 * The hosts screen's audio card and personal-mix overlay (WP9b): types and
 * pure helpers.
 *
 * Shapes mirror `audio.hosts_view` from
 * server/baja_studio/modules/audio/hosts_view.py. Audio owns these facts;
 * the hosts screen joins them with `hosts.seats` by seat id for the name
 * and colour.
 */

export type SeatAudioState = "live" | "to_production" | "muted" | "not_mapped";

export interface SeatAudio {
  seat_id: number;
  channel: string | null;
  channel_label: string | null;
  iem_bus: string;
  iem_label: string;
  /** null while the mixer is offline or has not said enough yet. */
  state: SeatAudioState | null;
  production_to_you: boolean;
}

export interface MixRow {
  channel: string;
  label: string;
  link: "linked" | "paused" | "unlinked";
  main_level: number | null;
  main_db: string | null;
  /** How far the seat hears it above or below the main mix; null unless linked. */
  offset_db: number | null;
  send_level: number | null;
  send_db: string | null;
}

export interface PadChange {
  seq: number;
  seat_id: number;
  channel: string;
  label: string;
  offset_db: number;
}

export interface SeatAudioView {
  mixer_online: boolean;
  can_adjust: boolean;
  step_db: number;
  talkback: { active: boolean; label: string };
  seats: Record<string, SeatAudio>;
  mixes: Record<string, MixRow[]>;
  pad_change: PadChange | null;
}

/** How long the personal-mix overlay stays open without a touch (brief §3). */
export const OVERLAY_IDLE_CLOSE_MS = 20_000;
/** Holding + or −: the first repeat after this long, then one per `HOLD_REPEAT_MS`. */
export const HOLD_FIRST_REPEAT_MS = 400;
export const HOLD_REPEAT_MS = 250;
/** How long the "Ana: PC AUDIO +2 dB" toast stays after a pad press. */
export const PAD_TOAST_MS = 3000;

const STATE_WORDS: Record<SeatAudioState, string> = {
  live: "LIVE",
  to_production: "TO PRODUCTION",
  muted: "MUTED",
  not_mapped: "not mapped",
};

/** What the card says for a seat; "…" while the mixer has not said enough. */
export function seatStateWord(state: SeatAudioState | null): string {
  return state === null ? "…" : STATE_WORDS[state];
}

/** "+2 dB", "−1.5 dB", "0 dB": an offset as the overlay and the toast show it. */
export function formatOffset(offsetDb: number): string {
  const rounded = Math.round(offsetDb * 10) / 10;
  if (rounded === 0) return "0 dB";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)} dB`;
}

/** Whether a row follows the main mix with an offset, or is set on its own ("independent"). */
export function followsMain(row: MixRow): boolean {
  return row.link === "linked";
}
