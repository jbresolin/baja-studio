/**
 * The playout status line: OBS connection, how old the orchestrator sync
 * is (re-rendered every second), and this page's client id.
 */

import { useEffect, useState } from "react";

import { formatAge, type PlayoutState } from "../../lib/playout";

const OBS_BADGE: Record<string, string> = {
  connected: "badge-ok",
  connecting: "badge-degraded",
  auth_failed: "badge-degraded",
  offline: "badge-offline",
  not_configured: "badge-offline",
};

interface StatusLineProps {
  state: PlayoutState;
  clientId: string | null;
}

export function StatusLine({ state, clientId }: StatusLineProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const sync = state.sync_status;
  const folderErrors = Object.entries(state.folders.errors);
  return (
    <div className="playout-status" role="status" aria-label="Playout status">
      <span>
        OBS{" "}
        <span className={`badge ${OBS_BADGE[state.obs.connection] ?? "badge-offline"}`}>
          {state.obs.connection.replace("_", " ")}
        </span>
        {state.obs.current_scene && <span className="muted"> · {state.obs.current_scene}</span>}
        {state.obs.message && <span className="muted"> · {state.obs.message}</span>}
      </span>
      <span data-testid="sync-age">
        Sync:{" "}
        {sync.state === "disabled"
          ? "off"
          : `${sync.state}, ${formatAge(sync.last_success_at, now)}`}
        {sync.error && <span className="muted"> · {sync.error}</span>}
      </span>
      {folderErrors.length > 0 && (
        <span className="field-error">
          {folderErrors.map(([path, error]) => `${path}: ${error}`).join("; ")}
        </span>
      )}
      <span className="muted">
        This page is <strong>web:{clientId ?? "…"}</strong> · press <kbd>?</kbd> for keys
      </span>
    </div>
  );
}
