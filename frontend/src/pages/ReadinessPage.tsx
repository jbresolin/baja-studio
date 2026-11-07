/**
 * The Readiness page (/readiness): every module's pre-event checks in one
 * list, green/amber/red, with the fix for each problem. Read standing at the
 * rack, so it is one column on a phone.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import {
  clockTime,
  runAsText,
  type CheckResultView,
  type LastRun,
  type ReadinessState,
} from "../lib/readiness";
import { useModuleState, useSendCommand } from "../lib/stateHooks";

export function ReadinessPage() {
  const readiness = useModuleState<ReadinessState>("readiness");
  const sendCommand = useSendCommand();
  const [error, setError] = useState<string | null>(null);

  if (!readiness) return <p className="muted">Waiting for the studio service…</p>;
  const run = readiness.last_run;

  async function runChecks() {
    setError(null);
    try {
      await sendCommand("readiness", "run");
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  return (
    <div className="readiness">
      <div className="button-row">
        <button onClick={() => void runChecks()} disabled={readiness.running}>
          {readiness.running ? "Checking…" : "Run all checks"}
        </button>
        {run && <CopyAsText key={run.number} run={run} />}
        <ScheduleLine schedule={readiness.schedule} />
      </div>
      {error && <div className="notice notice-error">{error}</div>}
      {readiness.running && <Progress progress={readiness.progress} />}
      {run ? (
        <RunResults run={run} />
      ) : (
        !readiness.running && <p className="muted">No checks run yet since the studio started.</p>
      )}
    </div>
  );
}

function ScheduleLine({ schedule }: { schedule: ReadinessState["schedule"] }) {
  if (!schedule.enabled) {
    return (
      <span className="muted">
        Scheduled runs off (<Link to="/settings/readiness">settings</Link>)
      </span>
    );
  }
  return (
    <span className="muted" data-testid="schedule">
      Every {schedule.interval_minutes} min
      {schedule.next_run_at ? `, next at ${clockTime(schedule.next_run_at)}` : ""}
    </span>
  );
}

function Progress({ progress }: { progress: ReadinessState["progress"] }) {
  return (
    <ul className="readiness-progress" data-testid="progress">
      {Object.entries(progress).map(([module, { done, total }]) => (
        <li key={module}>
          {module} {done}/{total}
        </li>
      ))}
    </ul>
  );
}

/** The banner, then one group per module. */
export function RunResults({ run }: { run: LastRun }) {
  return (
    <>
      <ReadinessBanner run={run} />
      <p className="muted" data-testid="run-time">
        Checked at {clockTime(run.started_at)} in {run.duration_s.toFixed(1)} s
        {run.trigger === "scheduled" ? " (scheduled)" : ""}
      </p>
      {run.modules.map((module) => (
        <section className="readiness-module" key={module.name}>
          <h2>{module.title}</h2>
          <ul className="readiness-checks">
            {module.checks.map((check) => (
              <CheckLine key={check.key} check={check} />
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

/** "READY" in green or "NOT READY (n fails, m warnings)" in red. */
export function ReadinessBanner({ run, link = false }: { run: LastRun; link?: boolean }) {
  const text =
    run.ready && run.warnings > 0
      ? `${run.banner} · ${run.warnings} warning${run.warnings === 1 ? "" : "s"}`
      : run.banner;
  return (
    <div
      className={`readiness-banner ${run.ready ? "readiness-ready" : "readiness-not-ready"}`}
      data-testid="readiness-banner"
    >
      {link ? <Link to="/readiness">{text}</Link> : text}
    </div>
  );
}

function CheckLine({ check }: { check: CheckResultView }) {
  return (
    <li className={`readiness-check readiness-${check.status}`} data-testid={`check-${check.key}`}>
      <span className={`readiness-dot readiness-dot-${check.status}`} aria-label={check.status} />
      <div>
        <div className="readiness-label">{check.label}</div>
        {check.detail && <div className="readiness-detail">{check.detail}</div>}
        {check.fix && (
          <div className="readiness-fix">
            {check.fix}
            {check.link && (
              <>
                {" "}
                <Link to={check.link}>Open</Link>
              </>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

function CopyAsText({ run }: { run: LastRun }) {
  const [copied, setCopied] = useState<boolean | null>(null);

  async function copy() {
    setCopied(await copyText(runAsText(run)));
  }

  return (
    <button onClick={() => void copy()} type="button">
      {copied === null ? "Copy as text" : copied ? "Copied" : "Copy failed"}
    </button>
  );
}

/**
 * Put `text` on the clipboard. Over plain HTTP (a phone on the PC's address)
 * browsers hide `navigator.clipboard`, so fall back to a hidden text area.
 */
async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Refused (no permission): try the old way below.
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } finally {
    area.remove();
  }
}
