/**
 * Readiness state as the server publishes it (`state.readiness`), for the
 * Readiness page, home banner and toast tests.
 */

import type { CheckResultView, LastRun, ReadinessState } from "../src/lib/readiness";

export function check(
  module: string,
  id: string,
  status: CheckResultView["status"],
  extra: Partial<CheckResultView> = {},
): CheckResultView {
  return {
    key: `${module}.${id}`,
    module,
    id,
    label: `Label of ${id}`,
    status,
    detail: `detail of ${id}`,
    fix: status === "ok" ? "" : `fix for ${id}`,
    link: null,
    duration_s: 0.1,
    ...extra,
  };
}

/** A run with an audio group (mixer failing, M-VAVE warning) and a gateway group (all ok). */
export function lastRun(extra: Partial<LastRun> = {}): LastRun {
  return {
    number: 1,
    trigger: "manual",
    source: "web:alice",
    started_at: "2026-11-07T15:00:00+00:00",
    duration_s: 1.25,
    ready: false,
    banner: "NOT READY (1 fail, 1 warning)",
    fails: 1,
    warnings: 1,
    newly_failed: [],
    modules: [
      {
        name: "audio",
        title: "Audio",
        checks: [
          check("audio", "mixer", "fail", {
            label: "XR18 answers",
            detail: "offline: no answer",
            fix: "Check the XR18's network cable.",
            link: "/audio/config",
          }),
          check("audio", "mvave", "warn", { label: "M-VAVE control surface" }),
        ],
      },
      { name: "gateway", title: "Gateway", checks: [check("gateway", "caddy", "ok")] },
    ],
    ...extra,
  };
}

export const READY_RUN = lastRun({
  ready: true,
  banner: "READY",
  fails: 0,
  warnings: 0,
  modules: [{ name: "gateway", title: "Gateway", checks: [check("gateway", "caddy", "ok")] }],
});

export function readinessState(extra: Partial<ReadinessState> = {}): ReadinessState {
  return {
    running: false,
    progress: {},
    last_run: null,
    schedule: { enabled: false, interval_minutes: 5, next_run_at: null },
    ...extra,
  };
}
