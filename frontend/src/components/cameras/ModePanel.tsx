/**
 * Mode of an agent-owned camera: Manual, Auto (with its behaviour profile)
 * or Hold. The agent may refuse Auto (`tracker_not_ready`); its reason is shown.
 */

import { useState } from "react";

import type { CameraView } from "../../lib/cameras";

interface ModePanelProps {
  camera: CameraView;
  run: (command: string, args: Record<string, unknown>) => Promise<unknown>;
}

const MODES = [
  { mode: "manual", label: "Manual" },
  { mode: "auto", label: "Auto" },
  { mode: "hold", label: "Hold" },
] as const;

export function ModePanel({ camera, run }: ModePanelProps) {
  const [resumeAfterMs, setResumeAfterMs] = useState("3000");
  const [zone, setZone] = useState("");
  const [priorityTargets, setPriorityTargets] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inFallback = camera.route === "fallback";

  async function setMode(mode: string) {
    setError(null);
    const args: Record<string, unknown> = { mode };
    if (mode === "auto") {
      // A profile replaces the agent's previous one, so it is sent whole.
      const targets = priorityTargets
        .split(",")
        .map((target) => target.trim())
        .filter(Boolean);
      args.profile = {
        resume_after_ms: Number(resumeAfterMs) || 0,
        ...(zone.trim() && { zone: zone.trim() }),
        ...(targets.length > 0 && { priority_targets: targets }),
      };
    }
    try {
      await run("set_mode", args);
    } catch (refused) {
      setError((refused as Error).message);
    }
  }

  return (
    <div className="mode-panel">
      <div className="button-row">
        {MODES.map(({ mode, label }) => (
          <button
            key={mode}
            aria-pressed={
              camera.mode === mode || (mode === "auto" && camera.mode === "auto_paused")
            }
            disabled={inFallback}
            onClick={() => void setMode(mode)}
          >
            {label}
          </button>
        ))}
      </div>
      {inFallback && (
        <p className="muted">The agent is away; the camera is driven directly, without modes.</p>
      )}
      <label className="field">
        <span>Resume after (ms)</span>
        <input
          type="number"
          min={0}
          max={600000}
          step={500}
          value={resumeAfterMs}
          onChange={(event) => setResumeAfterMs(event.target.value)}
        />
      </label>
      <label className="field">
        <span>Zone</span>
        <input value={zone} onChange={(event) => setZone(event.target.value)} />
      </label>
      <label className="field">
        <span>Priority targets</span>
        <input
          placeholder="car-12, car-7"
          value={priorityTargets}
          onChange={(event) => setPriorityTargets(event.target.value)}
        />
      </label>
      {error && (
        <div className="notice notice-error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
