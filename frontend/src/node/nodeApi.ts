/**
 * The camera node's page talks to the node it is served by (ADR 0023):
 * settings through the core settings API, commands and live state through
 * the state socket. With a token set on the node, every request carries it
 * (`X-Agent-Token`), and the socket's address does (`/ws?token=`), because
 * a browser cannot put a header on a WebSocket. The token is kept for this
 * tab only (sessionStorage), never in the page's address.
 */

export const TOKEN_KEY = "baja-camera-node.token";
export const MASK = "********";

export type VideoMode = "node" | "obs";

export interface UsbDeviceRef {
  path: string;
  name: string;
  index: number;
}

/** One camera of the node's settings (`node/settings.py`, NodeCameraSettings). */
export interface NodeCamera {
  id: string;
  label: string;
  enabled: boolean;
  mode: VideoMode;
  usb_device: UsbDeviceRef | null;
  video: {
    device: string;
    device_name: string;
    input_width: number;
    input_height: number;
    input_fps: number;
    output_url: string;
    encoder: string;
    bitrate_kbps: number;
    decoder: string;
  };
  obs: {
    port: number;
    password: string;
    source: string;
    filter_name: string;
    preview_source: string;
  };
  min_window: number;
  [other: string]: unknown;
}

/** The node's settings, as `/api/settings/node` shows them (secrets masked). */
export interface NodeSettingsValues {
  agent_id: string;
  name: string;
  token: string;
  port: number;
  ffmpeg_path: string;
  cameras: NodeCamera[];
  [other: string]: unknown;
}

export interface NodeSettingsDocument {
  version: number;
  values: NodeSettingsValues;
}

/** `state.node.cameras.<id>`: what a camera's driver publishes, and its details. */
export interface NodeCameraView {
  label?: string;
  mode?: VideoMode;
  status?: string;
  status_reason?: string;
  last_error?: string;
  moving?: boolean;
  crop?: { magnification: number; centre_x: number; centre_y: number } | null;
  image_controls?: string[];
  details?: {
    pipeline?: {
      state: string;
      reason: string;
      input_fps: number;
      output_fps: number;
      dropped_frames: number;
      bitrate_kbps: number;
      encoder: string | null;
      restarts: number;
      window_apply_ms: number | null;
    } | null;
    ffmpeg_log?: string[];
    output?: string;
    obs?: { connected: boolean; problem: string; source: string | null };
  };
}

export interface NodeState {
  cameras: Record<string, NodeCameraView>;
  order: string[];
  usb: { status: string; message: string; devices: UsbDeviceRef[] };
  system: { cpu_percent: number | null; memory_percent: number | null };
}

/** Thrown when the node wants a token (HTTP 401). */
export class TokenNeededError extends Error {}

export function savedToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function saveToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

/** The state socket's address, with the token when there is one. */
export function socketUrl(base: string, token: string): string {
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = savedToken();
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("X-Agent-Token", token);
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) throw new TokenNeededError("this node needs its token");
  const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    throw new Error(detail || response.statusText);
  }
  return body as T;
}

export function getNodeSettings(): Promise<NodeSettingsDocument> {
  return call<NodeSettingsDocument>("/api/settings/node");
}

export function saveNodeSettings(
  values: NodeSettingsValues,
  version: number,
): Promise<NodeSettingsDocument> {
  return call<NodeSettingsDocument>("/api/settings/node", {
    method: "PUT",
    body: JSON.stringify({ values, version }),
  });
}

export interface LogEntry {
  ts: string;
  level: string;
  message: string;
}

/** The newest `limit` lines of the node's log, oldest first. */
export function getLogTail(limit: number): Promise<LogEntry[]> {
  return call<LogEntry[]>(`/api/logs?limit=${limit}`);
}

/** Run one of the node's commands (`node/module.py`) and return its result. */
export async function nodeCommand<T>(
  command: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const answer = await call<{ result: T }>("/api/commands", {
    method: "POST",
    body: JSON.stringify({ module: "node", command, args, caller: "node-page" }),
  });
  return answer.result;
}

/** A new camera's settings: node mode, the next free id. */
export function newCamera(existing: NodeCamera[]): NodeCamera {
  let number = existing.length + 1;
  while (existing.some((camera) => camera.id === `cam${number}`)) number += 1;
  return {
    id: `cam${number}`,
    label: "",
    enabled: true,
    mode: "node",
    usb_device: null,
    video: {
      device: "",
      device_name: "",
      input_width: 3840,
      input_height: 2160,
      input_fps: 30,
      output_url: "",
      encoder: "auto",
      decoder: "mjpeg",
      bitrate_kbps: 8000,
    },
    obs: { port: 4455, password: "", source: "", filter_name: "Baja crop", preview_source: "" },
    min_window: 0.5,
  };
}
