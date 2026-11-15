/**
 * Add to OBS: the one place the Feeds page changes OBS, and only on a click.
 *
 * The first click asks the studio for OBS's scenes (`obs_scenes`); the
 * operator picks one and confirms; the studio then creates a media source
 * named after the feed's label, reading the local MediaMTX (`add_to_obs`).
 * The studio refuses a name OBS already has: an existing source is never
 * changed. Every answer, good or bad, is shown under the button.
 */

import { useState } from "react";

import { useSendCommand } from "../../lib/stateHooks";

interface AddToObsProps {
  feedName: string;
  sourceName: string;
}

type Step =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "picking"; scenes: string[]; scene: string }
  | { kind: "adding"; scenes: string[]; scene: string };

export function AddToObs({ feedName, sourceName }: AddToObsProps) {
  const sendCommand = useSendCommand();
  const [step, setStep] = useState<Step>({ kind: "idle" });
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);

  async function loadScenes() {
    setOutcome(null);
    setStep({ kind: "loading" });
    try {
      const reply = (await sendCommand("feeds", "obs_scenes")) as { scenes: string[] };
      setStep({ kind: "picking", scenes: reply.scenes, scene: reply.scenes[0] ?? "" });
    } catch (error) {
      setStep({ kind: "idle" });
      setOutcome({ ok: false, text: (error as Error).message });
    }
  }

  async function add(scenes: string[], scene: string) {
    setStep({ kind: "adding", scenes, scene });
    try {
      await sendCommand("feeds", "add_to_obs", { feed: feedName, scene });
      setStep({ kind: "idle" });
      setOutcome({ ok: true, text: `Added "${sourceName}" to the scene "${scene}" in OBS.` });
    } catch (error) {
      setStep({ kind: "picking", scenes, scene });
      setOutcome({ ok: false, text: (error as Error).message });
    }
  }

  return (
    <div className="add-to-obs">
      {(step.kind === "idle" || step.kind === "loading") && (
        <button type="button" disabled={step.kind === "loading"} onClick={() => void loadScenes()}>
          {step.kind === "loading" ? "Asking OBS…" : "Add to OBS"}
        </button>
      )}
      {(step.kind === "picking" || step.kind === "adding") && (
        <form
          className="command-form"
          onSubmit={(event) => {
            event.preventDefault();
            void add(step.scenes, step.scene);
          }}
        >
          <label>
            Scene{" "}
            <select
              aria-label={`OBS scene for ${feedName}`}
              value={step.scene}
              onChange={(event) =>
                setStep({ kind: "picking", scenes: step.scenes, scene: event.target.value })
              }
            >
              {step.scenes.map((scene) => (
                <option key={scene}>{scene}</option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={step.kind === "adding" || !step.scene}>
            {step.kind === "adding" ? "Adding…" : `Add "${sourceName}"`}
          </button>
          <button type="button" onClick={() => setStep({ kind: "idle" })}>
            Cancel
          </button>
        </form>
      )}
      {outcome && (
        <div className={outcome.ok ? "notice" : "notice notice-error"} role="status">
          {outcome.text}
        </div>
      )}
    </div>
  );
}
