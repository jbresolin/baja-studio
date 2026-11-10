/**
 * Stream-start marker and the aired log export: chapter text for YouTube
 * (copyable) and the full log as a JSON download. The download also carries
 * the session's messages to the hosts (WP9), when the hosts module is on.
 */

import { useState } from "react";

import type { HostsMessage } from "../../lib/hosts";
import type { AiredExport as AiredExportResult } from "../../lib/playout";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface AiredExportProps {
  marker: string | null;
  run: Run;
  /** The hosts messages of the session, or null when the hosts module is off. */
  hostsMessages: () => Promise<HostsMessage[] | null>;
}

export function AiredExport({ marker, run, hostsMessages }: AiredExportProps) {
  const [result, setResult] = useState<AiredExportResult | null>(null);
  const [messages, setMessages] = useState<HostsMessage[] | null>(null);

  async function exportLog() {
    setResult((await run("export_aired")) as AiredExportResult);
    setMessages(await hostsMessages());
  }

  function download() {
    if (result === null) return;
    const body = messages === null ? result : { ...result, hosts_messages: messages };
    const blob = new Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "aired-log.json";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  return (
    <div className="aired-export">
      <h2>Aired log</h2>
      <div className="muted">
        Stream start: {marker ? new Date(marker).toLocaleTimeString() : "not marked"}
      </div>
      <div className="button-row">
        <button onClick={() => void run("mark_stream_start")}>Mark stream start</button>
        <button onClick={() => void exportLog()}>Export aired log</button>
      </div>
      {result && (
        <>
          <p className="muted">
            {result.entries.length} airings
            {messages !== null && `, ${messages.length} messages to the hosts`}.
          </p>
          {result.chapters !== null ? (
            <textarea
              aria-label="Chapters"
              readOnly
              rows={Math.min(10, result.chapters.split("\n").length + 1)}
              value={result.chapters}
            />
          ) : (
            <p className="muted">Mark the stream start to get chapter offsets.</p>
          )}
          <div className="button-row">
            {result.chapters !== null && (
              <button onClick={() => void navigator.clipboard?.writeText(result.chapters ?? "")}>
                Copy chapters
              </button>
            )}
            <button onClick={download}>Download JSON</button>
          </div>
        </>
      )}
    </div>
  );
}
