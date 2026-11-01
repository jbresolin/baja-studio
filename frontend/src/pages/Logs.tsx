/** Live tail of the service log, streamed over Server-Sent Events. */

import { useEffect, useMemo, useState } from "react";

import type { LogEntry } from "../lib/api";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
const MAX_ENTRIES_SHOWN = 1000;

function levelRank(level: string): number {
  const rank = LEVELS.indexOf(level as (typeof LEVELS)[number]);
  return rank === -1 ? 0 : rank;
}

export function Logs() {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [minimumLevel, setMinimumLevel] = useState<string>("INFO");
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    // EventSource reconnects by itself; the server re-sends recent history on
    // each connect, so we start over rather than show duplicates.
    const source = new EventSource("/api/logs/stream");
    source.onopen = () => {
      setConnected(true);
      setEntries([]);
    };
    source.onerror = () => setConnected(false);
    source.onmessage = (event: MessageEvent<string>) => {
      const entry = JSON.parse(event.data) as LogEntry;
      setEntries((current) => [...current.slice(-(MAX_ENTRIES_SHOWN - 1)), entry]);
    };
    return () => source.close();
  }, []);

  const visible = useMemo(
    () => entries.filter((entry) => levelRank(entry.level) >= levelRank(minimumLevel)),
    [entries, minimumLevel],
  );

  return (
    <div className="logs-page">
      <div className="page-header">
        <h1>Logs</h1>
        <label>
          Level{" "}
          <select value={minimumLevel} onChange={(event) => setMinimumLevel(event.target.value)}>
            {LEVELS.map((level) => (
              <option key={level}>{level}</option>
            ))}
          </select>
        </label>
        <span className={connected ? "connection connection-open" : "connection connection-closed"}>
          ● {connected ? "streaming" : "reconnecting…"}
        </span>
      </div>
      <table className="dense-table log-table">
        <tbody>
          {[...visible].reverse().map((entry, index) => (
            <tr key={`${entry.ts}-${index}`} className={`log-${entry.level.toLowerCase()}`}>
              <td className="nowrap">{new Date(entry.ts).toLocaleTimeString()}</td>
              <td>{entry.level}</td>
              <td>{entry.studio_module ?? ""}</td>
              <td>
                {entry.message}
                {entry.traceback && <pre className="traceback">{entry.traceback}</pre>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
