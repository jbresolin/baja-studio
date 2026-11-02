/**
 * One configured agent on the Agents page: its link status, its cameras with
 * live mode and tracker state, and the per-agent controls (enable, token,
 * remove).
 */

import { useState } from "react";

import type { AgentView, CameraStateView } from "../lib/agents";
import { HealthBadge } from "./HealthBadge";

interface AgentCardProps {
  id: string;
  agent: AgentView;
  /** Runs an agents-module command; resolves when the server has answered. */
  run: (command: string, args: Record<string, unknown>) => Promise<void>;
}

export function AgentCard({ id, agent, run }: AgentCardProps) {
  const [token, setToken] = useState("");
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const labels = Object.fromEntries((agent.info?.cameras ?? []).map((c) => [c.id, c.label]));
  const cameraIds = Object.keys(agent.cameras).length
    ? Object.keys(agent.cameras)
    : Object.keys(labels);

  return (
    <section className="agent-card" data-testid={`agent-${id}`}>
      <div className="card-header">
        <span className="card-title">
          {id} <span className="muted">{`${agent.host}:${agent.port}`}</span>
        </span>
        <HealthBadge state={agent.status} />
      </div>
      {agent.reason && <div className="card-message">{agent.reason}</div>}
      {agent.info && (
        <div className="muted">
          {agent.info.name} · contract {agent.info.contract_version} · {agent.info.software_version}
          {agent.last_seen && ` · last heard ${new Date(agent.last_seen).toLocaleTimeString()}`}
        </div>
      )}

      {cameraIds.length > 0 && (
        <table className="dense-table agent-cameras">
          <thead>
            <tr>
              <th>Camera</th>
              <th>Status</th>
              <th>Mode</th>
              <th>Tracker</th>
              <th>Position</th>
            </tr>
          </thead>
          <tbody>
            {cameraIds.map((cameraId) => (
              <CameraRow
                key={cameraId}
                id={cameraId}
                label={labels[cameraId] ?? cameraId}
                state={agent.cameras[cameraId]}
                // An offline agent's last known state is shown greyed, not hidden.
                stale={agent.status !== "ok"}
              />
            ))}
          </tbody>
        </table>
      )}

      <div className="button-row">
        <label>
          <input
            type="checkbox"
            checked={agent.enabled}
            onChange={(event) => void run("update", { id, enabled: event.target.checked })}
          />{" "}
          Enabled
        </label>
        <input
          type="password"
          aria-label={`Token for ${id}`}
          placeholder="token (unchanged)"
          autoComplete="new-password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
        />
        <button
          disabled={!token}
          onClick={() => void run("update", { id, token }).then(() => setToken(""))}
        >
          Set token
        </button>
        {confirmingRemove ? (
          <>
            <button className="danger" onClick={() => void run("remove", { id })}>
              Really remove {id}
            </button>
            <button onClick={() => setConfirmingRemove(false)}>Keep</button>
          </>
        ) : (
          <button onClick={() => setConfirmingRemove(true)}>Remove</button>
        )}
      </div>
    </section>
  );
}

interface CameraRowProps {
  id: string;
  label: string;
  state: CameraStateView | undefined;
  stale: boolean;
}

function CameraRow({ id, label, state, stale }: CameraRowProps) {
  const position = state?.position;
  return (
    <tr className={stale || state?.status === "offline" ? "stale" : undefined}>
      <td>
        {label} <span className="muted">{id}</span>
      </td>
      <td>{state ? <HealthBadge state={state.status} /> : "–"}</td>
      <td data-testid={`mode-${id}`}>
        {state?.mode ?? "–"}
        {state?.moving && " (moving)"}
      </td>
      <td>
        {state?.tracker.state ?? "–"}
        {state?.tracker.target_id && ` ${state.tracker.target_id}`}
      </td>
      <td className="nowrap">
        {position
          ? `pan ${position.pan.toFixed(1)}° tilt ${position.tilt.toFixed(1)}° zoom ${position.zoom.toFixed(2)}`
          : "–"}
      </td>
    </tr>
  );
}
