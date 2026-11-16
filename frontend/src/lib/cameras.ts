/**
 * Shapes of the cameras module's live state (server/baja_studio/cameras/registry.py,
 * ADR 0003 decision 28) and of its settings (server/baja_studio/cameras/config.py).
 */

import type { CameraKind, CameraPosition, TrackerView } from "./agents";
import type { ImageCardView, UsbState } from "./imaging";

export type CameraStatus = "ok" | "degraded" | "offline";
/**
 * visca_ip and agent (WP2); uvc (the Pixy) and none (no motion, e.g. a fixed
 * S600): WP6a; obs_crop (a Crop/Pad filter in OBS): WP6b.
 */
export type DriverKind = "visca_ip" | "agent" | "uvc" | "none" | "obs_crop";
export type CameraMode = "manual" | "auto" | "auto_paused" | "hold";

export interface PresetName {
  n: number;
  label: string;
}

export interface Velocity {
  pan: number;
  tilt: number;
  zoom: number;
}

/** The arbiter's view of who drives the camera. */
export interface CameraControl {
  velocity_owner: string | null;
  velocity: Velocity | null;
  latched: boolean;
  last_source: string | null;
  last_command: string | null;
  last_command_ts: string | null;
}

export interface CameraCapabilities {
  kind: CameraKind;
  has_position_feedback: boolean;
  has_zoom: boolean;
  supports_auto: boolean;
  preset_max: number | null;
  /** Contract 1.1: an agent camera that serves snapshots of its whole frame (ADR 0023). */
  snapshot?: boolean;
}

/**
 * A crop camera's window (`state.cameras.cameras.<id>.crop`, ADR 0020): x, y,
 * w, h are fractions of the source (top-left and size). OBS crop cameras also
 * give the source's pixels; agent crop cameras do not know them.
 */
export interface CropView {
  x: number;
  y: number;
  w: number;
  h: number;
  centre_x: number;
  centre_y: number;
  zoom: number | null;
  magnification: number;
  source_width?: number;
  source_height?: number;
  filter?: string;
}

/**
 * Which OBS an `obs_crop` camera talks to (`state.cameras.cameras.<id>.obs`,
 * WP13): `address` is null for this PC's OBS (playout's), "host:port" for a
 * laptop's; `note` is what Camera setup should say (e.g. two cameras saved
 * with different passwords for one laptop). Never a password.
 */
export interface ObsConnectionView {
  remote: boolean;
  address: string | null;
  note: string;
}

/** `state.cameras.cameras.<id>`. */
export interface CameraView {
  label: string;
  owner: "studio" | "agent";
  driver: DriverKind;
  controller_address: number | null;
  obs_source: string | null;
  presets: PresetName[];
  usb_device?: UsbDeviceRef | null;
  status: CameraStatus;
  status_reason: string;
  route: "direct" | "agent" | "fallback";
  mode: CameraMode | null;
  tracker: TrackerView;
  position: CameraPosition | null;
  /** True when `position` is only where the camera was last told to go (the Pixy). */
  position_is_commanded?: boolean;
  moving: boolean;
  last_error: string;
  capabilities?: CameraCapabilities;
  control: CameraControl;
  /** Crop cameras only: where the window is. */
  crop?: CropView | null;
  /** OBS crop cameras only: which OBS (WP13). */
  obs?: ObsConnectionView | null;
}

/** One decoded frame on the controller's frame monitor. */
export interface MonitoredFrame {
  ts: string;
  hex: string;
  action: "move" | "stop" | "recall_preset" | "call" | "unknown";
  address?: number;
  pan?: number;
  tilt?: number;
  zoom?: number;
  n?: number;
}

/** `state.cameras.controllers.<id>`. */
export interface ControllerView {
  port: string;
  baud: number;
  status: CameraStatus;
  status_reason: string;
  last_call: { n: number; ts: string } | null;
  selected_camera: { camera: string; ts: string } | null;
  recent_frames: MonitoredFrame[];
}

export interface MotionSettings {
  joystick_deadband: number;
  ttl_ms: number;
  resend_interval_ms: number;
  joystick_zoom_velocity: number;
  controller_silence_ms: number;
}

export interface CamerasState {
  cameras: Record<string, CameraView>;
  controllers: Record<string, ControllerView>;
  /** Enabled cameras in display order (and Alt+1–9 order). */
  order: string[];
  motion: Partial<MotionSettings>;
  /** WP6a: image cards by camera id, and the USB cameras plugged in. */
  image?: Record<string, ImageCardView>;
  usb?: UsbState;
}

// --- settings (what the configuration page edits) ------------------------------------

export interface CameraAddress {
  host: string;
  port: number;
  agent_id: string;
  remote_camera_id: string;
}

export interface MotionLimits {
  pan_speed_scale: number;
  tilt_speed_scale: number;
  zoom_speed_scale: number;
  invert_pan: boolean;
  invert_tilt: boolean;
  pan_speed_max_step: number;
  tilt_speed_max_step: number;
}

/** A stored window of an `obs_crop` camera (its preset `n`). */
export interface CropPresetWindow {
  n: number;
  centre_x: number;
  centre_y: number;
  width: number;
}

/**
 * Which OBS holds an `obs_crop` camera's source (WP13, server: crop_config.ObsConnection):
 * this PC's (playout's, `remote` false) or another PC's at host:port. The
 * password is write-only: the server sends "********" for a saved one, and
 * that mask sent back means "keep it".
 */
export interface ObsConnection {
  remote: boolean;
  host: string;
  port: number;
  password: string;
}

export const OBS_WEBSOCKET_PORT = 4455;

/** How an `obs_crop` camera moves its OBS Crop/Pad filter (server: cameras/crop_config.py). */
export interface CropSettings {
  obs: ObsConnection;
  filter_name: string;
  min_window: number;
  aspect: string;
  update_hz: number;
  pan_speed: number;
  tilt_speed: number;
  /** Pan and tilt scaled by the window's width, so the picture moves as fast at any zoom. */
  speed_follows_zoom: boolean;
  zoom_speed: number;
  preview_source: string | null;
  windows: CropPresetWindow[];
}

/** Which USB camera a camera is (WP6a): its DirectShow path, and name + index for display. */
export interface UsbDeviceRef {
  path: string;
  name: string;
  index: number;
}

/** A saved image control: a raw value (null: leave as is) and auto (null: has none). */
export interface ImageSetting {
  value: number | null;
  auto: boolean | null;
}

/** A uvc camera's preset, in device units (the Pixy's own slots lose zoom). */
export interface StoredPosition {
  n: number;
  pan: number;
  tilt: number;
  zoom: number;
}

export interface CameraConfig {
  id: string;
  label: string;
  enabled: boolean;
  owner: "studio" | "agent";
  driver: DriverKind;
  address: CameraAddress;
  controller_address: number | null;
  obs_source: string | null;
  presets: PresetName[];
  /** The readiness checks warn while such a camera has no presets. */
  needs_presets: boolean;
  limits: MotionLimits;
  speed_curve: "linear" | "squared";
  direct_fallback: { driver: "visca_ip"; address: CameraAddress } | null;
  /** VISCA/IP only: one position inquiry every this many seconds; 0 = none. */
  position_poll_seconds: number;
  usb_device: UsbDeviceRef | null;
  /** Edited on the Image page, not in the camera form. */
  image: Record<string, ImageSetting>;
  /** Stored by the Cameras page's preset buttons (uvc cameras). */
  stored_positions: StoredPosition[];
  /** Used by `obs_crop` cameras only. */
  crop: CropSettings;
}

export type CallAction = "none" | "select_camera" | "recall_preset";

export interface CallBinding {
  n: number;
  action: CallAction;
  camera: string | null;
  preset: number | null;
}

export interface ControllerConfig {
  id: string;
  port: string;
  baud: number;
  protocol: "pelco_d";
  enabled: boolean;
  call_bindings: CallBinding[];
}

export interface CamerasSettingsValues {
  enabled: boolean;
  cameras: CameraConfig[];
  controllers: ControllerConfig[];
  motion: MotionSettings;
}

export const VISCA_IP_PORT = 52381;

/** The server's defaults for an `obs_crop` camera's settings. */
export function defaultCropSettings(): CropSettings {
  return {
    obs: { remote: false, host: "", port: OBS_WEBSOCKET_PORT, password: "" },
    filter_name: "Baja crop",
    min_window: 0.5,
    aspect: "16:9",
    update_hz: 25,
    pan_speed: 0.3,
    tilt_speed: 0.3,
    speed_follows_zoom: true,
    zoom_speed: 0.5,
    preview_source: null,
    windows: [],
  };
}

export function emptyAddress(): CameraAddress {
  return { host: "", port: VISCA_IP_PORT, agent_id: "", remote_camera_id: "" };
}

/** A new camera as the "Add camera" button starts it (the server's defaults). */
export function newCamera(id: string): CameraConfig {
  return {
    id,
    label: "",
    enabled: true,
    owner: "studio",
    driver: "visca_ip",
    address: emptyAddress(),
    controller_address: null,
    obs_source: null,
    presets: [],
    needs_presets: false,
    limits: {
      pan_speed_scale: 1,
      tilt_speed_scale: 1,
      zoom_speed_scale: 1,
      invert_pan: false,
      invert_tilt: false,
      pan_speed_max_step: 0x18,
      tilt_speed_max_step: 0x14,
    },
    speed_curve: "linear",
    direct_fallback: null,
    position_poll_seconds: 1,
    usb_device: null,
    image: {},
    stored_positions: [],
    crop: defaultCropSettings(),
  };
}

export function newController(id: string): ControllerConfig {
  return { id, port: "", baud: 2400, protocol: "pelco_d", enabled: true, call_bindings: [] };
}

/** "Preset 3" for n = 2: operators count presets from 1, like the controller's buttons. */
export function presetLabel(n: number, presets: PresetName[]): string {
  return presets.find((preset) => preset.n === n)?.label ?? `Preset ${n + 1}`;
}

/** The enabled cameras in display order, falling back to the state's own order. */
export function orderedCameraIds(state: CamerasState | undefined): string[] {
  if (!state) return [];
  const known = Object.keys(state.cameras ?? {});
  const ordered = (state.order ?? []).filter((id) => known.includes(id));
  return [...ordered, ...known.filter((id) => !ordered.includes(id))];
}

/** Where an OBS crop camera's source is, for the camera list: "this PC" or "host:port". */
export function obsLocation(obs: ObsConnection): string {
  if (!obs.remote) return "this PC";
  const host = obs.host.includes(":") ? `[${obs.host}]` : obs.host;
  return `${host}:${obs.port}`;
}
