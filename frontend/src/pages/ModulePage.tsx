/**
 * A generic page for any module: health, live state, recent commands and a
 * command console. Real modules get their own pages in later work packages;
 * this one stays useful for debugging all of them.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { HealthBadge } from "../components/HealthBadge";
import { getCommandLog, type CommandLogEntry } from "../lib/api";
import { useModuleState, useSendCommand } from "../lib/stateHooks";

interface QuickCommand {
  label: string;
  command: string;
  args?: Record<string, unknown>;
}

/** One-click commands for modules that have them (the demo modules, the gateway). */
const QUICK_COMMANDS: Record<string, QuickCommand[]> = {
  heartbeat: [
    { label: "Reset count", command: "reset" },
    { label: "Crash worker once", command: "crash_once" },
  ],
  echo: [
    { label: "Say hello", command: "say", args: { text: "hello" } },
    { label: "Kill child process", command: "kill_child" },
  ],
  gateway: [
    { label: "Apply config", command: "apply" },
    { label: "Test DNS", command: "test_dns" },
    { label: "Test TLS", command: "test_tls" },
  ],
};

interface HomeEntry {
  title: string;
  state: string;
  message: string;
}

export function ModulePage() {
  const { name = "" } = useParams();
  const home = useModuleState<{ modules: Record<string, HomeEntry> }>("home");
  const moduleState = useModuleState<unknown>(name);
  const sendCommand = useSendCommand();
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  const [commandText, setCommandText] = useState("");
  const [argsText, setArgsText] = useState("{}");
  const [history, setHistory] = useState<CommandLogEntry[]>([]);
  const entry = home?.modules[name];

  const refreshHistory = useCallback(() => {
    getCommandLog(name, 10)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [name]);

  useEffect(refreshHistory, [refreshHistory]);

  async function run(command: string, args: Record<string, unknown> = {}) {
    try {
      const result = await sendCommand(name, command, args);
      setOutcome({ ok: true, text: `${command}: ${JSON.stringify(result ?? "ok")}` });
    } catch (error) {
      setOutcome({ ok: false, text: `${command}: ${(error as Error).message}` });
    }
    refreshHistory();
  }

  function runTypedCommand() {
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(argsText || "{}") as Record<string, unknown>;
    } catch {
      setOutcome({ ok: false, text: 'arguments must be JSON, e.g. {"text": "hi"}' });
      return;
    }
    void run(commandText.trim(), args);
  }

  return (
    <div className="module-page">
      <div className="page-header">
        <h1>{entry?.title ?? name}</h1>
        {entry && <HealthBadge state={entry.state} />}
        <Link to={`/settings/${name}`}>Settings</Link>
      </div>
      {entry?.message && <div className="card-message">{entry.message}</div>}

      {(QUICK_COMMANDS[name] ?? []).length > 0 && (
        <div className="button-row">
          {QUICK_COMMANDS[name]!.map((quick) => (
            <button key={quick.label} onClick={() => void run(quick.command, quick.args)}>
              {quick.label}
            </button>
          ))}
        </div>
      )}

      <form
        className="command-form"
        onSubmit={(event) => {
          event.preventDefault();
          runTypedCommand();
        }}
      >
        <input
          aria-label="Command"
          placeholder="command"
          value={commandText}
          onChange={(event) => setCommandText(event.target.value)}
        />
        <input
          aria-label="Arguments (JSON)"
          placeholder='{"arg": "value"}'
          value={argsText}
          onChange={(event) => setArgsText(event.target.value)}
        />
        <button type="submit" disabled={!commandText.trim()}>
          Send
        </button>
      </form>
      {outcome && (
        <div className={outcome.ok ? "notice" : "notice notice-error"}>{outcome.text}</div>
      )}

      <h2>Live state</h2>
      <pre className="state-view">{JSON.stringify(moduleState ?? null, null, 2)}</pre>

      <h2>Recent commands</h2>
      <table className="dense-table">
        <tbody>
          {history.map((item) => (
            <tr key={`${item.ts}-${item.command}-${item.source}`}>
              <td>{new Date(item.ts).toLocaleTimeString()}</td>
              <td>{item.command}</td>
              <td>{item.source}</td>
              <td>{item.result}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
