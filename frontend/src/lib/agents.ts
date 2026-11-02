/**
 * Shapes of the agents module's live state (server/baja_studio/agents/registry.py)
 * and of the agent API objects inside it (docs/agent-api.md).
 */

export interface TrackerView {
  state: "idle" | "searching" | "tracking" | "lost";
  target_id?: string | null;
  confidence?: number | null;
}

/** CameraState: pushed by the agent at least twice a second. */
export interface CameraStateView {
  status: "ok" | "degraded" | "offline";
  mode: "manual" | "auto" | "auto_paused" | "hold";
  tracker: TrackerView;
  position: { pan: number; tilt: number; zoom: number } | null;
  moving: boolean;
  last_command_source?: string | null;
  last_command_ts?: string | null;
}

/** CameraInfo: what a camera is, from /agent/info. */
export interface CameraInfoView {
  id: string;
  label: string;
  kind: "ptz" | "crop" | "fixed";
  driver: string;
}

export interface AgentInfoView {
  agent_id: string;
  name: string;
  contract_version: string;
  software_version: string;
  cameras: CameraInfoView[];
}

export interface AgentEventView {
  ts: string;
  camera_id: string | null;
  event: string;
  detail: Record<string, unknown>;
}

/** One configured agent as the studio sees it. */
export interface AgentView {
  host: string;
  port: number;
  enabled: boolean;
  /** "connecting", "ok", "degraded", "offline" or "disabled". */
  status: string;
  reason: string;
  info: AgentInfoView | null;
  cameras: Record<string, CameraStateView>;
  events: AgentEventView[];
  last_seen: string | null;
}

export interface ScanResult {
  host: string;
  port: number;
  agent_id: string;
  name: string;
  contract_version: string;
  cameras: string[];
  already_added: boolean;
}

export interface AgentsState {
  agents: Record<string, AgentView>;
  scan: { running: boolean; cidr: string; found: ScanResult[]; error: string };
}
