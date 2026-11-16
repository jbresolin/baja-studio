/**
 * Add to OBS: the one place the Feeds page changes OBS, and only on a click.
 *
 * The first click asks the studio for OBS's scenes (`obs_scenes`); the
 * operator picks one and how OBS reads the feed, and confirms; the studio
 * then creates the source, reading the local MediaMTX (`add_to_obs`):
 * - a Media Source over RTSP, named after the feed's label (the default);
 * - or a Browser Source over WebRTC, the lowest delay, named after the label
 *   with " (WebRTC)", so a feed can have both and they can be compared.
 * The studio refuses a name OBS already has: an existing source is never
 * changed (so a source added before an update keeps its old settings until
 * it is deleted in OBS and added again). Every answer, good or bad, is shown
 * under the button.
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

/** How OBS reads the feed: the `source` argument of `add_to_obs`. */
type SourceKind = "media" | "browser";

const SOURCE_KINDS: { value: SourceKind; label: string }[] = [
  { value: "media", label: "Media Source (RTSP)" },
  { value: "browser", label: "Browser Source (WebRTC, lowest delay)" },
];

/** The name the studio gives the new source (see obs_media_source.py). */
export function obsSourceName(sourceName: string, kind: SourceKind): string {
  return kind === "browser" ? `${sourceName} (WebRTC)` : sourceName;
}

export function AddToObs({ feedName, sourceName }: AddToObsProps) {
  const sendCommand = useSendCommand();
  const [step, setStep] = useState<Step>({ kind: "idle" });
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  const [sourceKind, setSourceKind] = useState<SourceKind>("media");
  const newName = obsSourceName(sourceName, sourceKind);

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
      await sendCommand("feeds", "add_to_obs", { feed: feedName, scene, source: sourceKind });
      setStep({ kind: "idle" });
      setOutcome({ ok: true, text: `Added "${newName}" to the scene "${scene}" in OBS.` });
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
          <label>
            As{" "}
            <select
              aria-label={`OBS source type for ${feedName}`}
              value={sourceKind}
              onChange={(event) => setSourceKind(event.target.value as SourceKind)}
            >
              {SOURCE_KINDS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={step.kind === "adding" || !step.scene}>
            {step.kind === "adding" ? "Adding…" : `Add "${newName}"`}
          </button>
          <button type="button" onClick={() => setStep({ kind: "idle" })}>
            Cancel
          </button>
        </form>
      )}
      {(step.kind === "picking" || step.kind === "adding") && (
        <p className="muted">
          Media Source: decoded by OBS itself. Browser Source: the least delay, decoded in OBS's
          browser. A source added earlier keeps the settings it was made with; delete it in OBS and
          add it again to get the newest ones.
        </p>
      )}
      {outcome && (
        <div className={outcome.ok ? "notice" : "notice notice-error"} role="status">
          {outcome.text}
        </div>
      )}
    </div>
  );
}
