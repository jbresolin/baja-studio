/**
 * A controller's last decoded frames, live (WP3 brief §4.2): wiring and baud
 * problems show here as no frames, garbage addresses or unknown actions.
 *
 * "Copy last 100 raw frames" asks the server for the raw bytes, to attach to
 * the bench report and turn into test fixtures. The studio is reached over
 * plain http, where browsers may refuse the clipboard, so the hex is also
 * shown in a box to select by hand.
 */

import { useState } from "react";

import type { MonitoredFrame } from "../../lib/cameras";

interface FrameMonitorProps {
  controllerId: string;
  frames: MonitoredFrame[];
  readRawFrames: () => Promise<string[]>;
}

function describe(frame: MonitoredFrame): string {
  switch (frame.action) {
    case "move":
      return `move pan ${frame.pan} tilt ${frame.tilt} zoom ${frame.zoom}`;
    case "recall_preset":
      return `recall preset ${(frame.n ?? 0) + 1}`;
    case "call":
      return `Call ${frame.n}`;
    default:
      return frame.action;
  }
}

export function FrameMonitor({ controllerId, frames, readRawFrames }: FrameMonitorProps) {
  const [raw, setRaw] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function copyRaw() {
    setError(null);
    try {
      const text = (await readRawFrames()).join("\n");
      setRaw(text);
      await navigator.clipboard?.writeText(text).catch(() => undefined);
    } catch (refused) {
      setError((refused as Error).message);
    }
  }

  return (
    <div className="frame-monitor" data-testid={`frames-${controllerId}`}>
      {frames.length === 0 ? (
        <p className="muted">No frames yet. Move the stick; nothing here means wiring or baud.</p>
      ) : (
        <table className="dense-table">
          <tbody>
            {frames.map((frame, index) => (
              <tr
                key={`${frame.ts}-${index}`}
                className={frame.action === "unknown" ? "log-warning" : ""}
              >
                <td className="nowrap muted">{new Date(frame.ts).toLocaleTimeString()}</td>
                <td className="nowrap">
                  {frame.address !== undefined ? `addr ${frame.address}` : ""}
                </td>
                <td>{describe(frame)}</td>
                <td className="nowrap muted mono">{frame.hex}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="button-row">
        <button onClick={() => void copyRaw()}>Copy last 100 raw frames</button>
        {error && <span className="field-error">{error}</span>}
      </div>
      {raw !== null && (
        <textarea
          aria-label={`Raw frames from ${controllerId}`}
          className="mono"
          readOnly
          rows={6}
          value={raw}
          onFocus={(event) => event.target.select()}
        />
      )}
    </div>
  );
}
