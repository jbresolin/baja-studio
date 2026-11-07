/**
 * Shapes of the cameras module's live state (server/baja_studio/cameras/registry.py,
 * ADR 0003 decision 28) and of its settings (server/baja_studio/cameras/config.py).
 */

import type { CameraKind, CameraPosition, TrackerView } from "./agents";

export type CameraStatus = "ok" | "degraded" | "offline";
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
}

/** `state.cameras.cameras.<id>`. */
export interface CameraView {
  label: string;
  owner: "studio" | "agent";
  driver: "visca_ip" | "agent";
  controller_address: number | null;
  obs_source: string | null;
  presets: PresetName[];
  status: CameraStatus;
  status_reason: string;
  route: "direct" | "agent" | "fallback";
  mode: CameraMode | null;
  tracker: TrackerView;
  position: CameraPosition | null;
  moving: boolean;
  last_error: string;
  capabilities?: CameraCapabilities;
  control: CameraControl;
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

export interface CameraConfig {
  id: string;
  label: string;
  enabled: boolean;
  owner: "studio" | "agent";
  driver: "visca_ip" | "agent";
  address: CameraAddress;
  controller_address: number | null;
  obs_source: string | null;
  presets: PresetName[];
  /** The readiness checks warn while such a camera has no presets. */
  needs_presets: boolean;
  limits: MotionLimits;
  speed_curve: "linear" | "squared";
  direct_fallback: { driver: "visca_ip"; address: CameraAddress } | null;
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
