/**
 * The Agents page: every camera-laptop agent the studio talks to, live; a
 * subnet scan to find new ones; adding one by address; and the rest of the
 * agents settings through the usual schema-driven form.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AgentCard } from "../components/AgentCard";
import { HealthBadge } from "../components/HealthBadge";
import type { AgentsState } from "../lib/agents";
import { getSettings } from "../lib/api";
import { useModuleState, useSendCommand } from "../lib/stateHooks";
import { SectionEditor } from "./Settings";

interface HomeEntry {
  title: string;
  state: string;
  message: string;
}

export function AgentsPage() {
  const agents = useModuleState<AgentsState>("agents");
  const home = useModuleState<{ modules: Record<string, HomeEntry> }>("home");
  const sendCommand = useSendCommand();
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  const [cidr, setCidr] = useState("");
  const [address, setAddress] = useState({ host: "", port: "8890", id: "", token: "" });
  // Bumped after commands that change the saved agent list, so the settings
  // form below reloads instead of saving on top of an older version.
  const [settingsGeneration, setSettingsGeneration] = useState(0);
  const health = home?.modules.agents;
  const scan = agents?.scan;

  // Start the scan box with the network saved in settings.
  useEffect(() => {
    getSettings("agents")
      .then((document) => setCidr((current) => current || String(document.values.scan_cidr ?? "")))
      .catch(() => undefined);
  }, []);

  async function run(command: string, args: Record<string, unknown>): Promise<void> {
    try {
      await sendCommand("agents", command, args);
      setOutcome(null);
      if (command !== "scan" && command !== "probe") setSettingsGeneration((n) => n + 1);
    } catch (error) {
      setOutcome({ ok: false, text: `${command}: ${(error as Error).message}` });
    }
  }

  const entries = Object.entries(agents?.agents ?? {});

  return (
    <div className="module-page">
      <div className="page-header">
        <h1>{health?.title ?? "Camera agents"}</h1>
        {health && <HealthBadge state={health.state} />}
        <Link to="/settings/agents">Settings</Link>
      </div>
      {health?.message && <div className="card-message">{health.message}</div>}
      {outcome && (
        <div className={outcome.ok ? "notice" : "notice notice-error"} role="status">
          {outcome.text}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="muted">No agents yet. Scan the network or add one by its address below.</p>
      ) : (
        entries.map(([id, agent]) => <AgentCard key={id} id={id} agent={agent} run={run} />)
      )}

      <h2>Find agents</h2>
      <form
        className="command-form"
        onSubmit={(event) => {
          event.preventDefault();
          void run("scan", { cidr });
        }}
      >
        <input
          aria-label="Network to scan"
          placeholder="e.g. 192.168.1.0/24"
          value={cidr}
          onChange={(event) => setCidr(event.target.value)}
        />
        <button type="submit" disabled={!cidr.trim() || scan?.running}>
          {scan?.running ? "Scanning…" : "Scan subnet"}
        </button>
      </form>
      {scan && !scan.running && scan.cidr && (
        <table className="dense-table" data-testid="scan-results">
          <tbody>
            {scan.found.length === 0 && (
              <tr>
                <td className="muted">No agents answered on {scan.cidr}.</td>
              </tr>
            )}
            {scan.found.map((found) => (
              <tr key={found.host}>
                <td>{found.host}</td>
                <td>
                  {found.name} <span className="muted">({found.agent_id})</span>
                </td>
                <td>contract {found.contract_version}</td>
                <td>{found.cameras.join(", ")}</td>
                <td>
                  <button
                    disabled={found.already_added}
                    onClick={() =>
                      void run("add", { host: found.host, port: found.port, id: found.agent_id })
                    }
                  >
                    {found.already_added ? "Added" : "Add"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Add by address</h2>
      <form
        className="command-form"
        onSubmit={(event) => {
          event.preventDefault();
          void run("add", {
            host: address.host.trim(),
            port: Number(address.port) || 8890,
            ...(address.id.trim() && { id: address.id.trim() }),
            ...(address.token && { token: address.token }),
          }).then(() => setAddress({ host: "", port: "8890", id: "", token: "" }));
        }}
      >
        <input
          aria-label="Agent host"
          placeholder="host or IP"
          value={address.host}
          onChange={(event) => setAddress({ ...address, host: event.target.value })}
        />
        <input
          aria-label="Agent port"
          className="narrow"
          value={address.port}
          onChange={(event) => setAddress({ ...address, port: event.target.value })}
        />
        <input
          aria-label="Agent id"
          placeholder="id (optional)"
          value={address.id}
          onChange={(event) => setAddress({ ...address, id: event.target.value })}
        />
        <input
          aria-label="Agent token"
          type="password"
          placeholder="token (if the agent needs one)"
          autoComplete="new-password"
          value={address.token}
          onChange={(event) => setAddress({ ...address, token: event.target.value })}
        />
        <button type="submit" disabled={!address.host.trim()}>
          Add
        </button>
      </form>

      <h2>Settings</h2>
      <SectionEditor key={settingsGeneration} section="agents" />
    </div>
  );
}
