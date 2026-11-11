/**
 * Types and pure helpers for the Playout page (WP5 brief §6, WP5b).
 *
 * The shapes mirror the server: `playout` state from
 * server/baja_studio/modules/playout/publisher.py, clips from `public_clip`
 * in server/baja_studio/playout/clip_view.py. Everything here is plain
 * TypeScript, testable without React.
 */

export interface PlayoutClip {
  id: number;
  source: "orchestrator" | "manual";
  /** pipeline (in the manifest), unmatched (receiver root, not in it) or manual. */
  kind: ClipKind;
  /** The library group: the prova, "Unmatched", or the folder's label. */
  group: string;
  title: string;
  file_path: string;
  relative_path: string;
  folder_label: string;
  missing: boolean;
  queueable: boolean;
  duration_s: number | null;
  /** Video frame rate from ffprobe (null: unknown, frame steps use 30). */
  fps?: number | null;
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

export type ClipKind = "pipeline" | "unmatched" | "manual";

export interface QueueEntry {
  entry_id: number;
  clip_id: number;
  added_by: string;
  added_at: string;
  playlist_id: number | null;
  playlist: string | null;
  title: string;
  duration_s: number | null;
  missing: boolean;
}

/** What the media controls need about the clip on air (WP5c). */
export interface TransportView {
  state: "playing" | "paused" | "other";
  position_s: number;
  fps: number;
  speed_percent: number;
}

export interface ProgramView {
  clip: PlayoutClip;
  slot: "a" | "b";
  started_at: string;
  position_s: number;
  duration_s: number | null;
  remaining_s: number | null;
  /** 100, or slow motion (50, 25). */
  speed_percent?: number;
  transport?: TransportView;
}

/** A live speed change: one runs at a time; `target` is the last wish (LIVESTACK-56). */
export interface SpeedChangeView {
  changing: boolean;
  target: number | null;
  /** Why the last change failed (nothing was cut), or null. */
  error: string | null;
}

export interface PreviewView {
  clip: PlayoutClip;
  slot: "a" | "b";
  speed_percent?: number;
}

export interface OnAirAction {
  module: string;
  command: string;
  args: Record<string, unknown>;
}

export interface OnAirActions {
  on_clip_start: OnAirAction[];
  on_clip_end: OnAirAction[];
}

export interface Playlist {
  id: number;
  name: string;
  shuffle: boolean;
  /** Replaces the settings' on-air actions for this playlist's clips; null: use the settings'. */
  on_air: OnAirActions | null;
  clip_ids: number[];
  created_at: string;
}

export type ReturnMode = "previous" | "chosen";

export interface ReturnTarget {
  mode: ReturnMode;
  chosen_scene: string | null;
  previous_scene: string | null;
  /** Where a return goes now, or null. */
  scene: string | null;
  /** Why there is no return target (shown on the strip), or null. */
  warning: string | null;
}

export interface HostsView {
  on_air: { label: string; remaining_s: number | null; duration_s: number | null } | null;
  next: { label: string; duration_s: number | null }[];
  queue_remaining_s: number;
}

export type ObsConnection =
  "not_configured" | "connecting" | "connected" | "offline" | "auth_failed";

export interface PlayoutState {
  obs: {
    connection: ObsConnection;
    message: string;
    current_scene: string | null;
    address?: string;
    /** OBS's scenes without the two playback ones (the "chosen scene" list). */
    scenes?: string[];
  };
  program: ProgramView | null;
  preview: PreviewView | null;
  /** The replay speed the next cue gets while nothing is cued. */
  cue_speed_percent?: number;
  speed_change?: SpeedChangeView;
  queue: QueueEntry[];
  queue_total_s: number;
  /** Queue entries whose duration is unknown. */
  queue_unknown: number;
  library: Record<string, PlayoutClip>;
  library_summary: {
    total: number;
    missing: number;
    /** Present files not measured by ffprobe yet (a rescan counts down from all of them). */
    pending_probe?: number;
    unplayed: number;
    unmatched: number;
    per_group: Record<string, number>;
  };
  playlists: Playlist[];
  return_target: ReturnTarget | null;
  tools: {
    ffprobe: string | null;
    ffmpeg: string | null;
    /** Where each was found: bundled, settings or PATH. */
    sources?: Record<string, string>;
    problem: string | null;
  };
  cue_problem: string | null;
  /** The latest problem to show once as a toast; a new id each time. */
  notice: { id: number; message: string } | null;
  hosts_view: HostsView;
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
    playlist: string | null;
    offset: string | null;
  }[];
  chapters: string | null;
}

export type CountdownColour = "white" | "amber" | "red";

export const AMBER_AT_SECONDS = 10;
export const RED_AT_SECONDS = 5;
/** The group of files in the receiver root that the manifest does not know. */
export const UNMATCHED_GROUP = "Unmatched";
/** Tooltip of an unmatched clip. */
export const UNMATCHED_HINT = "file not in the orchestrator manifest";

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

/** `m:ss.t`: a position on the scrub bar, to a tenth of a second. */
export function formatPosition(seconds: number): string {
  const tenths = Math.max(0, Math.round(seconds * 10));
  return `${formatDuration(Math.floor(tenths / 10))}.${tenths % 10}`;
}

/** A duration, or "?" when it is unknown (ffprobe missing or not run yet). */
export function formatKnownDuration(seconds: number | null | undefined): string {
  return seconds === null || seconds === undefined ? "?" : formatDuration(seconds);
}

/**
 * How much plays after the clip on air: the cued clip plus the queue.
 * Unknown durations are counted apart, and shown as "+?".
 */
export function remainingAfterOnAir(
  preview: PreviewView | null,
  queue: { duration_s: number | null }[],
): { seconds: number; unknown: number } {
  const upcoming = [...(preview ? [preview.clip] : []), ...queue];
  return {
    seconds: upcoming.reduce((total, item) => total + (item.duration_s ?? 0), 0),
    unknown: upcoming.filter((item) => item.duration_s === null).length,
  };
}

/** "−1:05", "−1:05 +?" when some durations are unknown, "+?" when none is known. */
export function formatRemaining(seconds: number, unknown: number): string {
  if (seconds === 0 && unknown > 0) return "+?";
  return `−${formatDuration(seconds)}${unknown > 0 ? " +?" : ""}`;
}

/** Fraction of the clip on air already played, 0..1 (0 when unknown). */
export function programProgress(program: ProgramView | null): number {
  if (!program || !program.duration_s) return 0;
  return Math.min(1, Math.max(0, program.position_s / program.duration_s));
}

/**
 * The second line of a library row: prova · carro N [team] for pipeline
 * clips, the folder for others; null when the title already says it.
 */
export function clipDetails(clip: PlayoutClip): string | null {
  let text: string;
  if (clip.kind === "pipeline") {
    const parts = [clip.prova, clip.car_number !== null ? `carro ${clip.car_number}` : null];
    text = parts.filter(Boolean).join(" · ");
    if (clip.team) text = `${text} [${clip.team}]`;
  } else {
    text = clip.kind === "unmatched" ? clip.relative_path : clip.folder_label;
  }
  return text && !clip.title.includes(text) ? text : null;
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
  /** The group's name: a prova, a folder label, or "Unmatched". */
  name: string;
  kind: ClipKind;
  clips: PlayoutClip[];
}

const KIND_ORDER: Record<ClipKind, number> = { pipeline: 0, manual: 1, unmatched: 2 };

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
 * The library in groups: provas first, then labelled folders, then
 * "Unmatched" (left out with `hideUnmatched`); each filtered by `filter` and
 * by every word of `search`. Inside a group: car number, then pass, then title.
 */
export function groupLibrary(
  library: Record<string, PlayoutClip>,
  search: string,
  filter: LibraryFilter,
  hideUnmatched = false,
): ClipGroup[] {
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  const groups = new Map<string, { kind: ClipKind; clips: PlayoutClip[] }>();
  for (const clip of Object.values(library)) {
    if (hideUnmatched && clip.kind === "unmatched") continue;
    if (!matchesFilter(clip, filter) || !matchesSearch(clip, words)) continue;
    const group = groups.get(clip.group) ?? { kind: clip.kind, clips: [] };
    group.clips.push(clip);
    groups.set(clip.group, group);
  }
  const ordered = [...groups.entries()].sort(
    ([a, groupA], [b, groupB]) =>
      KIND_ORDER[groupA.kind] - KIND_ORDER[groupB.kind] || a.localeCompare(b),
  );
  return ordered.map(([name, { kind, clips }]) => ({
    name,
    kind,
    clips: clips.sort(
      (a, b) =>
        (a.car_number ?? 0) - (b.car_number ?? 0) ||
        (a.passagem ?? 0) - (b.passagem ?? 0) ||
        a.title.localeCompare(b.title),
    ),
  }));
}

export type TransportKeyAction =
  "transport_toggle" | "transport_back" | "transport_forward" | "frame_back" | "frame_forward";

export type PlayoutKeyAction =
  | "take"
  | "skip"
  | "drop_next"
  | "return"
  | "replay_last"
  | "remove_selected"
  | "focus_search"
  | "toggle_legend"
  | TransportKeyAction;

/** The keyboard legend, in the order shown. Same keys as the 2026 tool where it had them. */
export const PLAYOUT_KEY_LEGEND: [string, string][] = [
  ["Space", "Take (cues the next clip first if nothing is cued)"],
  ["→", "Skip: end the clip on air now, as if it had finished"],
  ["Shift+→ / Backspace", "Drop the next clip (the cued one, else the head of the queue)"],
  ["R", "Return to the return scene"],
  ["L", "Replay last clip"],
  ["Delete", "Remove selected queue entries"],
  ["/", "Search the library"],
  ["?", "Show or hide this legend"],
];

/** Keys that work only while the media controls are unlocked (they act on air). */
export const TRANSPORT_KEY_LEGEND: [string, string][] = [
  ["K", "Pause / play the clip on air"],
  ["J / L", "Back / forward 5 s (L is not Replay last while unlocked)"],
  [", / .", "One frame back / forward (while paused)"],
];

/** Seconds a −5 s / +5 s press moves. */
export const NUDGE_SECONDS = 5;
/** How long the unlock control must be held before the media controls work. */
export const HOLD_TO_ENABLE_MS = 2000;
/** The replay speeds offered, in percent of normal. */
export const REPLAY_SPEEDS = [100, 50, 25] as const;

/** The transport command for a transport key, as `transport` args. */
export function transportArgs(action: TransportKeyAction): Record<string, unknown> {
  switch (action) {
    case "transport_toggle":
      return { action: "toggle" };
    case "transport_back":
      return { action: "nudge", seconds: -NUDGE_SECONDS };
    case "transport_forward":
      return { action: "nudge", seconds: NUDGE_SECONDS };
    case "frame_back":
      return { action: "step", frames: -1 };
    case "frame_forward":
      return { action: "step", frames: 1 };
  }
}

function transportKeyAction(key: string): TransportKeyAction | null {
  switch (key) {
    case "k":
    case "K":
      return "transport_toggle";
    case "j":
    case "J":
      return "transport_back";
    case "l":
    case "L":
      return "transport_forward";
    case ",":
      return "frame_back";
    case ".":
      return "frame_forward";
    default:
      return null;
  }
}

/**
 * Which action a key press means, or null. Nothing fires while the operator
 * types in a field (`targetIsEditable`), or with Ctrl/Alt/Meta held. With
 * the media controls unlocked (`transportEnabled`), K, J, L, "," and "."
 * drive the clip on air, and L is no longer "Replay last".
 */
export function playoutKeyAction(event: {
  key: string;
  targetIsEditable: boolean;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
  transportEnabled?: boolean;
}): PlayoutKeyAction | null {
  if (event.targetIsEditable || event.ctrlKey || event.altKey || event.metaKey) return null;
  if (event.transportEnabled) {
    const action = transportKeyAction(event.key);
    if (action !== null) return action;
  }
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
