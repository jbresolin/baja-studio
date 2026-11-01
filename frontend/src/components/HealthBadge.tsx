import type { HealthState } from "../lib/api";

/** A coloured pill showing a module's health ("ok", "degraded", "offline", "stopped"). */
export function HealthBadge({ state }: { state: HealthState | string }) {
  return <span className={`badge badge-${state}`}>{state}</span>;
}
