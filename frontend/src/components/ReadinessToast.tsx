import { useState } from "react";
import { Link } from "react-router-dom";

import { newlyFailedChecks, type CheckResultView, type ReadinessState } from "../lib/readiness";
import { useModuleState } from "../lib/stateHooks";

/**
 * Shown on every page when a scheduled readiness run finds a check that
 * passed last time failing now. Only new runs count: opening a page never
 * toasts about a run that finished before it was opened.
 */
export function ReadinessToast() {
  const readiness = useModuleState<ReadinessState>("readiness");
  // The run number seen last render; `undefined` until the first snapshot.
  const [seenRun, setSeenRun] = useState<number | null | undefined>(undefined);
  const [failed, setFailed] = useState<CheckResultView[]>([]);

  const run = readiness?.last_run ?? null;
  const number = run?.number ?? null;
  if (readiness !== undefined && number !== seenRun) {
    // Adjusting state while rendering, as React suggests for "what changed since last time".
    setSeenRun(number);
    if (seenRun !== undefined && run?.trigger === "scheduled") {
      const newlyRed = newlyFailedChecks(run);
      if (newlyRed.length > 0) setFailed(newlyRed);
    }
  }

  if (failed.length === 0) return null;
  return (
    <div className="toast toast-error readiness-toast" role="alert" data-testid="readiness-toast">
      <strong>
        Readiness: {failed.length === 1 ? "a check" : `${failed.length} checks`} went red
      </strong>
      <ul>
        {failed.map((check) => (
          <li key={check.key}>
            {check.label}: {check.detail}
          </li>
        ))}
      </ul>
      <Link to="/readiness" onClick={() => setFailed([])}>
        Open readiness
      </Link>{" "}
      <button type="button" onClick={() => setFailed([])}>
        Dismiss
      </button>
    </div>
  );
}
