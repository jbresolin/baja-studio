/**
 * Program and preview: what is on air (with a countdown going white → amber
 * at 10 s → red at 5 s), what is cued (playable in the browser without
 * touching OBS), and the transport buttons.
 */

import {
  clipMediaUrl,
  countdownColour,
  formatDuration,
  type PlayoutState,
} from "../../lib/playout";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface ProgramStripProps {
  program: PlayoutState["program"];
}

/** The now-playing strip, always visible (also at phone width). */
export function ProgramStrip({ program }: ProgramStripProps) {
  if (program === null) {
    return (
      <div className="program-strip idle" aria-label="Program">
        <span className="muted">Nothing from playout on air</span>
      </div>
    );
  }
  const colour = countdownColour(program.remaining_s);
  return (
    <div className="program-strip" aria-label="Program">
      <span className="on-air">ON AIR</span>
      <span className="program-title">{program.clip.title}</span>
      <span className="muted">
        {formatDuration(program.position_s)} / {formatDuration(program.duration_s)}
      </span>
      <span className={`countdown countdown-${colour}`} data-testid="countdown">
        -{formatDuration(program.remaining_s)}
      </span>
    </div>
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
                {formatDuration(preview.clip.duration_s)} · slot {preview.slot.toUpperCase()}
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
    </section>
  );
}
