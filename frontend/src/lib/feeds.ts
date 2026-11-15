/**
 * Shapes of the feeds module's live state (server/baja_studio/modules/feeds/__init__.py)
 * and of its settings (modules/feeds/config.py), plus small formatting helpers.
 *
 * Feeds are edited through the settings API, never by command, so a publish
 * password never reaches the command log. The server sends a saved password
 * back masked (`SECRET_MASK`); sending the mask back keeps it unchanged.
 */

/** What the settings API answers in place of a saved password. */
export const SECRET_MASK = "********";

export type FeedKind = "drone" | "laptop" | "other";
export type FeedStatus = "live" | "waiting" | "unknown";

export interface PublishUrl {
  protocol: "RTMP" | "SRT";
  address: string;
  url: string;
}

/** The live part of a card, also used for senders on names that are not feeds. */
export interface FeedLive {
  status: FeedStatus;
  since: string | null;
  protocol: string | null;
  video_codec: string | null;
  width: number | null;
  height: number | null;
  audio_codec: string | null;
  bitrate_kbps: number | null;
  readers: number;
}

/** One card on the Feeds page (feed_status.py). */
export interface FeedCardState extends FeedLive {
  id: string;
  name: string;
  label: string;
  obs_source_name: string;
  kind: FeedKind;
  record: boolean;
  has_password: boolean;
  publish_urls: PublishUrl[];
  obs_url: string;
}

export interface PortConflict {
  protocol: string;
  port: number;
  purpose: string;
  holder: string;
  message: string;
}

export interface FeedsState {
  service: {
    name: string;
    status: string;
    answering: boolean;
    version: string | null;
    api: string;
    message: string;
    port_conflicts: PortConflict[];
    log_tail: string[];
  };
  config: { path: string; written_at: string | null; waiting_for_reload: boolean; error: string };
  feeds: FeedCardState[];
  other_paths: (FeedLive & { name: string })[];
  refused: { name: string; at: string }[];
  addresses: string[];
  recordings: { folder: string; free_bytes: number | null; any_feed_records: boolean };
}

/** One feed as saved in the settings. */
export interface FeedSettings {
  id: string;
  name: string;
  label: string;
  kind: FeedKind;
  record: boolean;
  /** "" (none), SECRET_MASK (saved, unchanged) or a new password. */
  password: string;
}

/** "4.2 Mbit/s", "850 kbit/s", or "—" when not known yet. */
export function formatBitrate(kbps: number | null): string {
  if (kbps === null) return "—";
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbit/s`;
  return `${kbps} kbit/s`;
}

/** The time of day of an ISO timestamp, as this browser shows times ("" when none). */
export function formatSince(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString();
}

/** "1920×1080 H264 + MPEG-4 Audio", or "" when nothing is known. */
export function describePicture(live: FeedLive): string {
  const size = live.width && live.height ? `${live.width}×${live.height}` : "";
  const video = [size, live.video_codec].filter(Boolean).join(" ");
  return [video, live.audio_codec].filter(Boolean).join(" + ");
}

/** "123.4 GB free", or "" when not known. */
export function formatFreeSpace(bytes: number | null): string {
  if (bytes === null) return "";
  return `${(bytes / 1024 ** 3).toFixed(1)} GB free`;
}

/** Feed names as MediaMTX and the server accept them (config.py's FEED_NAME_PATTERN). */
export const FEED_NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,31}$/;

/** A new feed's id, like the server's `new_feed_id` (keeps its password through renames). */
export function newFeedId(): string {
  const random = Math.floor(Math.random() * 0xffffffff)
    .toString(16)
    .padStart(8, "0");
  return `feed-${random}`;
}
