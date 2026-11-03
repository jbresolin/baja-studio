/**
 * The last ten command-log entries for one camera, whoever sent them.
 *
 * The log lives in the database, not in the live state, so it is read again
 * whenever the camera's `control` changes (a new move, stop, preset or mode),
 * not on a timer.
 */

import { useEffect, useState } from "react";

import { getCommandLog, type CommandLogEntry } from "../../lib/api";

const SHOWN = 10;
const READ_DELAY_MS = 250;

interface CommandTailProps {
  camera: string;
  /** Changes whenever the camera's control state does; triggers a fresh read. */
  changeMarker: string | null;
}

export function CommandTail({ camera, changeMarker }: CommandTailProps) {
  const [entries, setEntries] = useState<CommandLogEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    // A short wait, so a burst of changes is read once, after it is logged.
    const timer = setTimeout(() => {
      getCommandLog("cameras", 200)
        .then((log) => {
          if (!cancelled) setEntries(log.filter((e) => e.args.camera === camera).slice(0, SHOWN));
        })
        .catch(() => undefined);
    }, READ_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [camera, changeMarker]);

  if (entries.length === 0) return <p className="muted">Nothing yet.</p>;
  return (
    <table className="dense-table command-tail">
      <tbody>
        {entries.map((entry, index) => (
          <tr key={`${entry.ts}-${index}`} className={entry.result === "ok" ? "" : "log-warning"}>
            <td className="nowrap muted">{new Date(entry.ts).toLocaleTimeString()}</td>
            <td className="nowrap">{entry.source}</td>
            <td>
              {entry.command}
              {entry.result !== "ok" && ` — ${entry.result}`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
