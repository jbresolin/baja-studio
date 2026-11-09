/**
 * Program and preview: the always-visible strip (its background fills with
 * the clip's progress; on the right, what is left of the clip and of the
 * queue after it), the cued clip (playable in the browser without touching
 * OBS), the transport buttons and the "Return to" control.
 */

import {
  clipMediaUrl,
  countdownColour,
  formatDuration,
  formatKnownDuration,
  formatRemaining,
  programProgress,
  remainingAfterOnAir,
  type PlayoutState,
  type ReturnMode,
} from "../../lib/playout";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface ProgramStripProps {
  state: PlayoutState;
}

/** The now-playing strip, always visible (also at phone width). */
export function ProgramStrip({ state }: ProgramStripProps) {
  const { program } = state;
  const after = remainingAfterOnAir(state.preview, state.queue);
  const warning = state.return_target?.warning ?? null;
  const queueText = (
    <span className="strip-queue" data-testid="queue-remaining">
      queue {formatRemaining(after.seconds, after.unknown)}
    </span>
  );
  if (program === null) {
    return (
      <div className="program-strip idle" aria-label="Program">
        <span className="muted">Nothing from playout on air</span>
        {warning && <StripWarning text={warning} />}
        {(after.seconds > 0 || after.unknown > 0) && queueText}
      </div>
    );
  }
  const colour = countdownColour(program.remaining_s);
  const progress = programProgress(program);
  return (
    <div
      className="program-strip"
      aria-label="Program"
      data-testid="program-strip"
      style={{ "--progress": `${(progress * 100).toFixed(1)}%` } as React.CSSProperties}
    >
      <span className="on-air">ON AIR</span>
      <span className="program-title">{program.clip.title}</span>
      {warning && <StripWarning text={warning} />}
      <span className="muted">
        {formatDuration(program.position_s)} / {formatKnownDuration(program.duration_s)}
      </span>
      <span className={`countdown countdown-${colour}`} data-testid="countdown">
        {program.remaining_s === null ? "−?" : `−${formatDuration(program.remaining_s)}`}
      </span>
      {queueText}
    </div>
  );
}

function StripWarning({ text }: { text: string }) {
  return (
    <span className="strip-warning" role="note" data-testid="return-warning">
      ⚠ {text}
    </span>
  );
}

interface ProgramPanelProps {
  state: PlayoutState;
  queueLength: number;
  onTake: () => void;
  run: Run;
}

export function ProgramPanel({ state, queueLength, onTake, run }: ProgramPanelProps) {
  const preview = state.preview;
  const connected = state.obs.connection === "connected";
  return (
    <section className="playout-program" aria-label="Program and preview">
      <div className="preview-card">
        <h2>Preview</h2>
        {preview ? (
          <>
            <div className="preview-title" data-testid="preview-title">
              {preview.clip.title}{" "}
              <span className="muted">
                {formatKnownDuration(preview.clip.duration_s)} · slot {preview.slot.toUpperCase()}
              </span>
            </div>
            <video
              key={preview.clip.id}
              className="preview-video"
              controls
              preload="metadata"
              src={clipMediaUrl(preview.clip.id)}
            />
          </>
        ) : (
          <p className="muted">Nothing cued. Cue loads the first clip of the queue.</p>
        )}
      </div>
      <div className="button-row transport">
        <button
          disabled={!connected || preview !== null || queueLength === 0}
          onClick={() => void run("cue_next")}
        >
          Cue
        </button>
        <button
          className="take-button"
          disabled={!connected || (preview === null && queueLength === 0)}
          onClick={onTake}
        >
          Take
        </button>
        <button
          disabled={!connected || state.program === null}
          title="End the clip on air now (→)"
          onClick={() => void run("skip")}
        >
          Skip
        </button>
        <button
          disabled={preview === null && queueLength === 0}
          title="Drop the next clip (Shift+→ or Backspace)"
          onClick={() => void run("drop_next")}
        >
          Drop next
        </button>
        <button disabled={!connected} onClick={() => void run("return_to_scene")}>
          Return
        </button>
        <button onClick={() => void run("replay_last")}>Replay last</button>
      </div>
      <label className="inline">
        <input
          type="checkbox"
          checked={state.auto_play_next}
          onChange={(event) => void run("set_auto_play_next", { enabled: event.target.checked })}
        />
        Auto-play next
      </label>
      <ReturnToControl state={state} run={run} />
    </section>
  );
}

/** Return to: ◉ previous scene / ○ chosen scene (a dropdown of OBS's scenes). */
function ReturnToControl({ state, run }: { state: PlayoutState; run: Run }) {
  const target = state.return_target;
  const mode: ReturnMode = target?.mode ?? "previous";
  const scenes = state.obs.scenes ?? [];
  const chosen = target?.chosen_scene ?? "";
  const set = (nextMode: ReturnMode, scene?: string) =>
    void run("set_return_target", { mode: nextMode, ...(scene !== undefined ? { scene } : {}) });
  return (
    <fieldset className="return-to" aria-label="Return to">
      <legend>Return to</legend>
      <label className="inline">
        <input
          type="radio"
          name="return-mode"
          checked={mode === "previous"}
          onChange={() => set("previous")}
        />
        previous scene
        <span className="muted">{target?.previous_scene ? ` (${target.previous_scene})` : ""}</span>
      </label>
      <label className="inline">
        <input
          type="radio"
          name="return-mode"
          checked={mode === "chosen"}
          onChange={() => set("chosen")}
        />
        chosen scene
        <select
          aria-label="Chosen return scene"
          value={chosen}
          onChange={(event) => set("chosen", event.target.value)}
        >
          <option value="">(pick a scene)</option>
          {[...new Set([...scenes, ...(chosen ? [chosen] : [])])].map((scene) => (
            <option key={scene} value={scene}>
              {scene}
            </option>
          ))}
        </select>
      </label>
    </fieldset>
  );
}
