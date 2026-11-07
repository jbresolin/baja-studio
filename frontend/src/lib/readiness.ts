/**
 * The readiness module's state (`state.readiness`, see
 * server/baja_studio/modules/readiness) and the text the "copy as text"
 * button puts on the clipboard for the crew chat.
 */

export type CheckStatus = "ok" | "warn" | "fail";

export interface CheckResultView {
  key: string;
  module: string;
  id: string;
  label: string;
  status: CheckStatus;
  detail: string;
  fix: string;
  link: string | null;
  duration_s: number;
}

export interface ModuleResults {
  name: string;
  title: string;
  checks: CheckResultView[];
}

export interface LastRun {
  number: number;
  trigger: "manual" | "scheduled";
  source: string;
  started_at: string;
  duration_s: number;
  ready: boolean;
  banner: string;
  fails: number;
  warnings: number;
  newly_failed: string[];
  modules: ModuleResults[];
}

export interface ReadinessState {
  running: boolean;
  progress: Record<string, { done: number; total: number }>;
  last_run: LastRun | null;
  schedule: { enabled: boolean; interval_minutes: number; next_run_at: string | null };
}

const STATUS_WORDS: Record<CheckStatus, string> = { ok: "OK", warn: "WARN", fail: "FAIL" };

/** "14:02:31" in the browser's time zone. */
export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Every check of a run, in page order. */
export function allChecks(run: LastRun): CheckResultView[] {
  return run.modules.flatMap((module) => module.checks);
}

/**
 * The run as plain text for the crew chat: the banner, then only what needs
 * attention (failures first), each with its fix; a ready studio is one line.
 */
export function runAsText(run: LastRun): string {
  const lines = [`Baja Studio: ${run.banner} (checked at ${clockTime(run.started_at)})`];
  const titles = new Map(run.modules.map((module) => [module.name, module.title]));
  const problems = allChecks(run)
    .filter((check) => check.status !== "ok")
    .sort((a, b) => (a.status === b.status ? 0 : a.status === "fail" ? -1 : 1));
  for (const check of problems) {
    lines.push(
      `${STATUS_WORDS[check.status]} ${titles.get(check.module) ?? check.module} · ${check.label}: ${check.detail}`,
    );
    if (check.fix) lines.push(`  → ${check.fix}`);
  }
  return lines.join("\n");
}

/** The checks that went from green to red, for the scheduled-run toast. */
export function newlyFailedChecks(run: LastRun): CheckResultView[] {
  const keys = new Set(run.newly_failed);
  return allChecks(run).filter((check) => keys.has(check.key));
}
