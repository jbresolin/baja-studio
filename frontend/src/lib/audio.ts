/**
 * Types and pure helpers for the audio pages (WP4 brief §5).
 *
 * Shapes mirror the server: `audio` state from
 * server/baja_studio/modules/audio/publisher.py, settings from
 * server/baja_studio/audio/config.py. The fader law is the XR18's, as in
 * server/baja_studio/adapters/xr18_levels.py.
 */

export const INPUTS = Array.from({ length: 16 }, (_, index) => String(index + 1));
export const AUX_BUSES = ["bus1", "bus2", "bus3", "bus4", "bus5", "bus6"] as const;
export const CHANNEL_IDS = [...INPUTS, "aux", "lr", ...AUX_BUSES];
export const SEND_SOURCES = [...INPUTS, "aux"];
export const POINT_BUSES = ["main", ...AUX_BUSES];
export const BUTTON_ROWS = ["rec", "solo", "mute", "sel"] as const;
export const ROW_NAMES: Record<string, string> = { rec: "R", solo: "S", mute: "M", sel: "□" };
export const STRIPS = ["1", "2", "3", "4", "5", "6", "7", "8"];

export type AuxBus = (typeof AUX_BUSES)[number];
export type LinkState = "linked" | "paused" | "unlinked";

export interface ChannelView {
  fader: number | null;
  db: string | null;
  muted: boolean | null;
  name: string | null;
  pan?: number | null;
  sends?: Record<string, number | null>;
}

export interface AudioState {
  mixer: {
    connection: "not_configured" | "connecting" | "ok" | "offline" | "disabled";
    host: string;
    message: string;
    name: string | null;
    model: string | null;
    firmware: string | null;
    packets_per_s: { sent: number; received: number };
  };
  channels: Record<string, ChannelView>;
  links: Record<string, Record<string, LinkState>>;
  stored_levels: Record<string, number>;
  macros: Record<string, { active: boolean }>;
  midi: {
    status: "disabled" | "starting" | "ok" | "offline";
    device: string | null;
    message: string;
    selected_bus: string | null;
    learn: { armed: boolean; by: string | null; captured: { control: string; at: string } | null };
    pickup: Record<string, number>;
  };
  last_source: Record<string, string>;
  d200: { last_seen: string | null };
  settings_version: number;
}

export interface ChannelConfig {
  label: string;
  color: string;
  visible: boolean;
}

export interface SendPoint {
  channel: string;
  bus: string;
}

export type MacroStep =
  | { op: "set_fader"; channel: string; level: number }
  | { op: "set_mute"; channel: string; muted: boolean }
  | { op: "set_aux"; channel: string; bus: AuxBus; level: number }
  | { op: "store_levels"; points: SendPoint[]; silence: boolean }
  | { op: "restore_levels"; points: SendPoint[] }
  | { op: "recall_scene"; snapshot: number };

export interface MacroConfig {
  id: string;
  label: string;
  color: string;
  mode: "toggle" | "trigger";
  on_steps: MacroStep[];
  off_steps: MacroStep[];
}

export interface SceneConfig {
  index: number;
  label: string;
  snapshot: number | null;
  macro: string | null;
}

export interface ButtonAction {
  type: "none" | "mute" | "send_toggle" | "macro";
  channel: string | null;
  bus: string | null;
  macro: string | null;
}

export interface KnobAction {
  target: "none" | "aux_send" | "pan";
  bus: AuxBus | null;
}

export interface AudioSettingsValues {
  enabled: boolean;
  mixer: { host: string; port: number; enabled: boolean };
  channels: Record<string, ChannelConfig>;
  midi: {
    enabled: boolean;
    device_name_match: string;
    faders: Record<string, string | null>;
    buttons: Record<string, ButtonAction>;
    knobs: Record<string, KnobAction>;
    row_buses: Record<string, string>;
    knob_step_db: number;
    fader_takeover: "pickup" | "jump";
    send_fader_feedback: boolean;
  };
  macros: MacroConfig[];
  scenes: SceneConfig[];
  d200: { enabled: boolean; state_poll_hint_s: number };
  talkback_bus: AuxBus;
}

// --- the XR18 fader law ------------------------------------------------------------

const SEGMENTS: [number, number, number][] = [
  [0.5, -10, 40],
  [0.25, -30, 80],
  [0.0625, -60, 160],
  [0, -90, 480],
];

/** Decibels for a level 0..1 (0 gives -90). */
export function levelToDb(level: number): number {
  const clamped = Math.min(1, Math.max(0, level));
  for (const [start, startDb, slope] of SEGMENTS) {
    if (clamped >= start) return startDb + (clamped - start) * slope;
  }
  return -90;
}

/** The level for `db`, clamped to -90..+10 dB. */
export function dbToLevel(db: number): number {
  const clamped = Math.min(10, Math.max(-90, db));
  for (const [start, startDb, slope] of SEGMENTS) {
    if (clamped >= startDb) return start + (clamped - startDb) / slope;
  }
  return 0;
}

/** "-inf" for off, "-7.5 dB" otherwise; "—" when unknown. */
export function formatDb(level: number | null | undefined): string {
  if (level === null || level === undefined) return "—";
  if (level <= 0) return "-inf";
  return `${levelToDb(level).toFixed(1)} dB`;
}

// --- names -----------------------------------------------------------------------------

/** "Ch 7", "Aux", "Main", "Bus 2". */
export function plainName(channel: string): string {
  if (channel.startsWith("bus")) return `Bus ${channel.slice(3)}`;
  if (channel === "aux") return "Aux";
  if (channel === "lr") return "Main";
  return `Ch ${channel}`;
}

/** The label set in the setup, else the plain name. */
export function channelLabel(
  channels: Record<string, ChannelConfig> | undefined,
  channel: string,
): string {
  return channels?.[channel]?.label || plainName(channel);
}

/** "HOST 1 → IEM HOST 1" for a send point, "HOST 1" for a fader. */
export function pointLabel(
  channels: Record<string, ChannelConfig> | undefined,
  point: SendPoint,
): string {
  const source = channelLabel(channels, point.channel);
  return point.bus === "main" ? source : `${source} → ${channelLabel(channels, point.bus)}`;
}

/** The order the link cells cycle through when tapped. */
export function nextLinkState(state: LinkState): LinkState {
  return state === "unlinked" ? "linked" : state === "linked" ? "paused" : "unlinked";
}

// --- the setup page's draft -------------------------------------------------------------

/** A new, empty macro with an id not used yet. */
export function newMacro(existing: MacroConfig[]): MacroConfig {
  const ids = new Set(existing.map((macro) => macro.id));
  let number = existing.length + 1;
  while (ids.has(`macro-${number}`)) number += 1;
  return {
    id: `macro-${number}`,
    label: "",
    color: "#3498db",
    mode: "toggle",
    on_steps: [],
    off_steps: [],
  };
}

/** A sensible first step of each kind, for the step editor's "add" menu. */
export function newStep(op: MacroStep["op"]): MacroStep {
  switch (op) {
    case "set_fader":
      return { op, channel: "1", level: 0.75 };
    case "set_mute":
      return { op, channel: "1", muted: true };
    case "set_aux":
      return { op, channel: "1", bus: "bus1", level: 0.75 };
    case "store_levels":
      return { op, points: [{ channel: "1", bus: "main" }], silence: true };
    case "restore_levels":
      return { op, points: [{ channel: "1", bus: "main" }] };
    case "recall_scene":
      return { op, snapshot: 1 };
  }
}

/** `items` with the one at `from` moved to `to` (for ▲/▼ reordering). */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (to < 0 || to >= items.length) return items;
  const copy = [...items];
  const [moved] = copy.splice(from, 1);
  copy.splice(to, 0, moved!);
  return copy;
}

/** The off steps that undo `on`: each close becomes an open and vice versa. */
export function mirrorSteps(on: MacroStep[]): MacroStep[] {
  return on.flatMap((step): MacroStep[] => {
    if (step.op === "store_levels") return [{ op: "restore_levels", points: step.points }];
    if (step.op === "restore_levels") {
      return [{ op: "store_levels", points: step.points, silence: true }];
    }
    if (step.op === "set_mute") return [{ ...step, muted: !step.muted }];
    return [];
  });
}

/** Whether two settings documents differ (the page's "unsaved changes" flag). */
export function differs(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) !== JSON.stringify(b);
}

/** The URLs a D200 button uses for this host, as the reference card lists them. */
export function d200Urls(origin: string, macroIds: string[]): { label: string; url: string }[] {
  const base = origin.replace(/\/$/, "");
  return [
    { label: "Toggle mute of channel 1", url: `${base}/action/mute/1` },
    { label: "Mute state image (poll, field muted = true)", url: `${base}/action/state/1` },
    { label: "Nudge channel 1 by +3 dB", url: `${base}/action/nudge/1/3` },
    {
      label: "Nudge channel 1's send to bus 4 by -3 dB",
      url: `${base}/action/nudge-aux/1/bus4/-3`,
    },
    { label: "Recall scene 2", url: `${base}/action/scene/2` },
    ...macroIds.flatMap((id) => [
      { label: `Run macro ${id}`, url: `${base}/action/macro/${id}` },
      {
        label: `Macro ${id} state (poll, field active = true)`,
        url: `${base}/action/macro-state/${id}`,
      },
    ]),
  ];
}
