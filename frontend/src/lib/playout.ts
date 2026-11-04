/**
 * Types and pure helpers for the Playout page (WP5 brief §6).
 *
 * The shapes mirror the server: `playout` state from
 * server/baja_studio/modules/playout/publisher.py, clips from `public_clip`
 * in server/baja_studio/playout/library.py. Everything here is plain
 * TypeScript, testable without React.
 */

export interface PlayoutClip {
  id: number;
  source: "orchestrator" | "manual";
  title: string;
  file_path: string;
  relative_path: string;
  folder_label: string;
  missing: boolean;
  queueable: boolean;
  duration_s: number | null;
  probe_error: string | null;
  prova: string | null;
  car_number: number | null;
  team: string | null;
  passagem: number | null;
  label: string | null;
  show_pass: boolean;
  notes: string;
  played_count: number;
  last_played_at: string | null;
  has_thumbnail: boolean;
  delivered_at: string | null;
}

export interface QueueEntry {
  entry_id: number;
  clip_id: number;
  added_by: string;
  added_at: string;
  title: string;
  duration_s: number | null;
  missing: boolean;
}

export interface ProgramView {
  clip: PlayoutClip;
  slot: "a" | "b";
  started_at: string;
  position_s: number;
  duration_s: number | null;
  remaining_s: number | null;
}

export interface PreviewView {
  clip: PlayoutClip;
  slot: "a" | "b";
}

export type ObsConnection =
  "not_configured" | "connecting" | "connected" | "offline" | "auth_failed";

export interface PlayoutState {
  obs: {
    connection: ObsConnection;
    message: string;
    current_scene: string | null;
    address?: string;
  };
  program: ProgramView | null;
  preview: PreviewView | null;
  queue: QueueEntry[];
  queue_total_s: number;
  library: Record<string, PlayoutClip>;
  library_summary: {
    total: number;
    missing: number;
    unplayed: number;
    per_prova: Record<string, number>;
  };
  sync_status: { state: string; last_success_at: string | null; error: string | null };
  folders: { errors: Record<string, string> };
  auto_play_next: boolean;
  take_confirmation: { requested_by: string; window_s: number } | null;
  export: { stream_start_marker: string | null };
}

export interface AiredExport {
  stream_start_marker: string | null;
  entries: {
    id: number;
    clip_id: number;
    title: string;
    started_at: string;
    ended_at: string | null;
    offset: string | null;
  }[];
  chapters: string | null;
}

export type CountdownColour = "white" | "amber" | "red";

export const AMBER_AT_SECONDS = 10;
export const RED_AT_SECONDS = 5;
/** The group manual clips (no prova) are listed under. */
export const MANUAL_GROUP = "Manual";

/** Countdown colour: white, amber from 10 s left, red from 5 s left. */
export function countdownColour(remainingSeconds: number | null): CountdownColour {
  if (remainingSeconds === null) return "white";
  if (remainingSeconds <= RED_AT_SECONDS) return "red";
  if (remainingSeconds <= AMBER_AT_SECONDS) return "amber";
  return "white";
}

/** `m:ss`, or `h:mm:ss` from an hour; "–" when unknown. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

/** "12 s ago", "3 min ago", or "never". */
export function formatAge(isoTime: string | null, nowMs: number): string {
  if (!isoTime) return "never";
  const seconds = Math.max(0, Math.round((nowMs - Date.parse(isoTime)) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

export type LibraryFilter = "all" | "unplayed" | "played" | "missing";

export interface ClipGroup {
  prova: string;
  clips: PlayoutClip[];
}

function matchesFilter(clip: PlayoutClip, filter: LibraryFilter): boolean {
  if (filter === "unplayed") return clip.played_count === 0 && !clip.missing;
  if (filter === "played") return clip.played_count > 0;
  if (filter === "missing") return clip.missing;
  return true;
}

function matchesSearch(clip: PlayoutClip, words: string[]): boolean {
  const haystack = [
    clip.title,
    clip.prova,
    clip.team,
    clip.car_number,
    clip.relative_path,
    clip.notes,
    clip.folder_label,
  ]
    .filter((part) => part !== null && part !== undefined)
    .join(" ")
    .toLowerCase();
  return words.every((word) => haystack.includes(word));
}

/**
 * The library grouped by prova (manual clips last), filtered by `filter` and
 * by every word of `search`. Inside a group: car number, then pass, then title.
 */
export function groupLibrary(
  library: Record<string, PlayoutClip>,
  search: string,
  filter: LibraryFilter,
): ClipGroup[] {
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  const groups = new Map<string, PlayoutClip[]>();
  for (const clip of Object.values(library)) {
    if (!matchesFilter(clip, filter) || !matchesSearch(clip, words)) continue;
    const prova = clip.prova ?? MANUAL_GROUP;
    groups.set(prova, [...(groups.get(prova) ?? []), clip]);
  }
  const ordered = [...groups.entries()].sort(([a], [b]) => {
    if (a === MANUAL_GROUP) return 1;
    if (b === MANUAL_GROUP) return -1;
    return a.localeCompare(b);
  });
  return ordered.map(([prova, clips]) => ({
    prova,
    clips: clips.sort(
      (a, b) =>
        (a.car_number ?? 0) - (b.car_number ?? 0) ||
        (a.passagem ?? 0) - (b.passagem ?? 0) ||
        a.title.localeCompare(b.title),
    ),
  }));
}

export type PlayoutKeyAction =
  | "take"
  | "skip"
  | "drop_next"
  | "return"
  | "replay_last"
  | "remove_selected"
  | "focus_search"
  | "toggle_legend";

/** The keyboard legend, in the order shown. Same keys as the 2026 tool where it had them. */
export const PLAYOUT_KEY_LEGEND: [string, string][] = [
  ["Space", "Take (cues the next clip first if nothing is cued)"],
  ["→", "Skip: end the clip on air now (post-clip applies)"],
  ["Shift+→ / Backspace", "Drop the next clip (the cued one, else the head of the queue)"],
  ["R", "Return to the return scene"],
  ["L", "Replay last clip"],
  ["Delete", "Remove selected queue entries"],
  ["/", "Search the library"],
  ["?", "Show or hide this legend"],
];

/**
 * Which action a key press means, or null. Nothing fires while the operator
 * types in a field (`targetIsEditable`), or with Ctrl/Alt/Meta held.
 */
export function playoutKeyAction(event: {
  key: string;
  targetIsEditable: boolean;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
}): PlayoutKeyAction | null {
  if (event.targetIsEditable || event.ctrlKey || event.altKey || event.metaKey) return null;
  switch (event.key) {
    case " ":
    case "Spacebar":
      return "take";
    case "ArrowRight":
      return event.shiftKey ? "drop_next" : "skip";
    case "Backspace":
      return "drop_next";
    case "r":
    case "R":
      return "return";
    case "l":
    case "L":
      return "replay_last";
    case "Delete":
      return "remove_selected";
    case "/":
      return "focus_search";
    case "?":
      return "toggle_legend";
    default:
      return null;
  }
}

/** True for elements where keys are typing, not shortcuts. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/** Where the service serves a clip's file and thumbnail. */
export function clipMediaUrl(clipId: number): string {
  return `/api/playout/clips/${clipId}/media`;
}

export function clipThumbnailUrl(clipId: number): string {
  return `/api/playout/clips/${clipId}/thumbnail`;
}
