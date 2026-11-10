/**
 * "Rescan library" on Playout setup (WP5c §1): forgets every duration, frame
 * rate and thumbnail and measures every file again. The progress counts the
 * present files measured so far, from `library_summary.pending_probe`.
 */

import { useState } from "react";

import type { PlayoutState } from "../../lib/playout";
import { useSendCommand } from "../../lib/stateHooks";

export function RescanLibrary({ playout }: { playout: PlayoutState | undefined }) {
  const sendCommand = useSendCommand();
  const [asked, setAsked] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const summary = playout?.library_summary;
  const pending = summary?.pending_probe ?? 0;
  const present = summary ? summary.total - summary.missing : 0;
  const rescanning = asked !== null && pending > 0;
  return (
    <div className="rescan">
      <button
        type="button"
        disabled={rescanning}
        onClick={() => {
          setProblem(null);
          sendCommand("playout", "rescan_library").then(
            (result) => setAsked((result as { files: number }).files),
            (error: Error) => setProblem(error.message),
          );
        }}
      >
        Rescan library
      </button>
      <span className="muted">
        Forgets cached durations and thumbnails and measures every file again.
      </span>
      {asked !== null && (
        <div role="status" data-testid="rescan-progress">
          {pending > 0 ? (
            <>
              <progress max={present || 1} value={present - pending} /> {present - pending} of{" "}
              {present} files measured
            </>
          ) : (
            `Rescan done: ${present} files measured.`
          )}
        </div>
      )}
      {problem && <div className="field-error">{problem}</div>}
    </div>
  );
}
