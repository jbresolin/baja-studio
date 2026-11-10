/**
 * Playout's settings page (/playout/settings, WP5b §1 and §4, WP5c §1):
 * forms for the folders, OBS, tools and on-air actions, "Rescan library",
 * and the whole settings as JSON under "Advanced". It is the one place
 * playout is configured: the generic Settings page shows playout's JSON
 * only and links here.
 *
 * The bench found a JSON box that took a list of strings for the folders;
 * here the forms make that shape impossible, and the server still checks
 * everything on save. A refused save lists each problem with its field
 * (`sources.local_folders.0: ...`). Saving sends the version the edit
 * started from; if someone saved meanwhile, the server refuses and nothing
 * is overwritten.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { FoldersForm } from "../components/playout/FoldersForm";
import { OnAirActionsEditor } from "../components/playout/OnAirActionsEditor";
import { RescanLibrary } from "../components/playout/RescanLibrary";
import {
  getSettings,
  saveSettings,
  SettingsConflictError,
  SettingsValidationError,
} from "../lib/api";
import type { PlayoutState } from "../lib/playout";
import { useAudioMacros, type PlayoutSettingsValues } from "../lib/playoutSettings";
import { useModuleState } from "../lib/stateHooks";

type Notice = { kind: "saved" | "error"; lines: string[] } | null;

const OBS_TEXT_FIELDS: [keyof PlayoutSettingsValues["obs"], string][] = [
  ["host", "OBS address (empty: not set up)"],
  ["scene_a", "Playback scene A"],
  ["source_a", "Media source in scene A"],
  ["scene_b", "Playback scene B"],
  ["source_b", "Media source in scene B"],
  ["cut_transition", "Cut transition name (pt-BR: Corte)"],
  ["fade_transition", "Fade transition name (pt-BR: Desvanecer)"],
];

export function PlayoutSettingsPage() {
  const playout = useModuleState<PlayoutState>("playout");
  const macros = useAudioMacros();
  const [version, setVersion] = useState<number | null>(null);
  const [saved, setSaved] = useState<PlayoutSettingsValues | null>(null);
  const [draft, setDraft] = useState<PlayoutSettingsValues | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [loadCount, setLoadCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSettings("playout")
      .then((document) => {
        if (cancelled) return;
        const values = document.values as PlayoutSettingsValues;
        setVersion(document.version);
        setSaved(values);
        setDraft(values);
      })
      .catch((error: Error) => {
        if (!cancelled) setNotice({ kind: "error", lines: [error.message] });
      });
    return () => {
      cancelled = true;
    };
  }, [loadCount]);

  if (!draft || !saved || version === null) {
    return notice ? <ErrorNotice lines={notice.lines} /> : <p className="muted">Loading…</p>;
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const set = <K extends keyof PlayoutSettingsValues>(key: K, value: PlayoutSettingsValues[K]) =>
    setDraft({ ...draft, [key]: value });
  const setObs = (field: string, value: unknown) =>
    set("obs", { ...draft.obs, [field]: value } as PlayoutSettingsValues["obs"]);

  async function save() {
    if (!draft || version === null) return;
    setNotice(null);
    try {
      const document = await saveSettings("playout", version, draft);
      setVersion(document.version);
      setSaved(document.values as PlayoutSettingsValues);
      setDraft(document.values as PlayoutSettingsValues);
      setNotice({ kind: "saved", lines: ["Saved."] });
    } catch (error) {
      if (error instanceof SettingsValidationError) {
        setNotice({
          kind: "error",
          lines: error.fieldErrors.map((problem) => `${problem.field}: ${problem.message}`),
        });
      } else if (error instanceof SettingsConflictError) {
        setNotice({
          kind: "error",
          lines: [
            "Someone else saved these settings meanwhile: load the latest and redo the change.",
          ],
        });
      } else {
        setNotice({ kind: "error", lines: [(error as Error).message] });
      }
    }
  }

  return (
    <form
      className="playout-settings"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <p>
        <Link to="/playout">← Playout</Link>
      </p>
      <h1>⚙ Playout setup</h1>
      <h2>Clip folders</h2>
      <FoldersForm
        folders={draft.sources.local_folders}
        onChange={(local_folders) => set("sources", { ...draft.sources, local_folders })}
      />

      <h2>OBS</h2>
      <div className="schema-form">
        {OBS_TEXT_FIELDS.map(([field, label]) => (
          <div className="field" key={field}>
            <label htmlFor={`obs-${field}`}>{label}</label>
            <input
              id={`obs-${field}`}
              value={String(draft.obs[field] ?? "")}
              onChange={(event) => setObs(field, event.target.value)}
            />
          </div>
        ))}
        <div className="field">
          <label htmlFor="obs-port">Port</label>
          <input
            id="obs-port"
            type="number"
            value={draft.obs.port}
            onChange={(event) => setObs("port", Number(event.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor="obs-password">Password</label>
          <input
            id="obs-password"
            type="password"
            autoComplete="new-password"
            value={draft.obs.password}
            onChange={(event) => setObs("password", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="obs-live-speed">Speed change on air</label>
          <select
            id="obs-live-speed"
            value={draft.obs.live_speed_change ?? "swap"}
            onChange={(event) => setObs("live_speed_change", event.target.value)}
          >
            <option value="swap">swap: other source, then cut (seamless)</option>
            <option value="reload">reload: same source, seek back (may hiccup)</option>
          </select>
          <div className="field-help">
            How a speed button on the media controls changes the clip on air.
          </div>
        </div>
        <div className="field">
          <label htmlFor="obs-post-clip">After a clip</label>
          <select
            id="obs-post-clip"
            value={draft.obs.post_clip}
            onChange={(event) => setObs("post_clip", event.target.value)}
          >
            <option value="return">return (cut back)</option>
            <option value="fade">fade back</option>
            <option value="hold">hold the last frame</option>
          </select>
          <div className="field-help">Where to return to is chosen on the playout page.</div>
        </div>
      </div>

      <h2>Tools</h2>
      <div className="schema-form">
        {(["ffprobe_path", "ffmpeg_path"] as const).map((field) => (
          <div className="field" key={field}>
            <label htmlFor={`tools-${field}`}>
              {field === "ffprobe_path" ? "ffprobe" : "ffmpeg"}
            </label>
            <input
              id={`tools-${field}`}
              value={draft.tools[field]}
              placeholder="bundled copy"
              onChange={(event) => set("tools", { ...draft.tools, [field]: event.target.value })}
            />
            <div className="field-help">
              Leave blank for the bundled copy.
              <ToolFound playout={playout} tool={field === "ffprobe_path" ? "ffprobe" : "ffmpeg"} />
            </div>
          </div>
        ))}
        {playout?.tools?.problem && <div className="field-error">{playout.tools.problem}</div>}
      </div>

      <h2>Library</h2>
      <RescanLibrary playout={playout} />

      <h2>On air actions</h2>
      <p className="muted">
        Audio macros to run when a clip goes to air and when it leaves. A playlist can have its own
        instead.
      </p>
      <OnAirActionsEditor
        value={draft.on_air_actions}
        macros={macros}
        onChange={(value) => set("on_air_actions", value)}
      />

      <details className="advanced-json">
        <summary>Advanced: all settings as JSON</summary>
        <AdvancedJson key={JSON.stringify(saved)} value={draft} onApply={setDraft} />
      </details>

      <div className="button-row">
        <button type="submit" disabled={!dirty}>
          Save
        </button>
        <button type="button" disabled={!dirty} onClick={() => setDraft(saved)}>
          Discard
        </button>
        <button type="button" onClick={() => setLoadCount((count) => count + 1)}>
          Load latest
        </button>
        <span className="muted">version {version}</span>
      </div>
      {notice &&
        (notice.kind === "saved" ? (
          <div className="notice" role="status">
            Saved.
          </div>
        ) : (
          <ErrorNotice lines={notice.lines} />
        ))}
    </form>
  );
}

/** "In use: bundled (C:\...\ffprobe.exe)", from the resolver the module itself uses. */
function ToolFound({ playout, tool }: { playout: PlayoutState | undefined; tool: string }) {
  const tools = playout?.tools;
  if (!tools) return null;
  const foundAt = tool === "ffprobe" ? tools.ffprobe : tools.ffmpeg;
  const source = tools.sources?.[tool];
  return (
    <span data-testid={`tool-found-${tool}`}>
      {" "}
      In use: {foundAt ? `${source ?? "found"} (${foundAt})` : "not found"}.
    </span>
  );
}

function ErrorNotice({ lines }: { lines: string[] }) {
  return (
    <div className="notice notice-error" role="alert" data-testid="settings-errors">
      {lines.length === 1 ? (
        lines[0]
      ) : (
        <ul>
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The whole draft as JSON; "Apply" copies valid JSON into the draft (still to be saved). */
function AdvancedJson({
  value,
  onApply,
}: {
  value: PlayoutSettingsValues;
  onApply: (value: PlayoutSettingsValues) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div>
      <textarea
        aria-label="Settings JSON"
        rows={16}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      {problem && <div className="field-error">{problem}</div>}
      <button
        type="button"
        onClick={() => {
          try {
            onApply(JSON.parse(text) as PlayoutSettingsValues);
            setProblem(null);
          } catch (error) {
            // Silently keeping the old value is how the bench lost a folder.
            setProblem(`Not valid JSON, nothing applied: ${(error as Error).message}`);
          }
        }}
      >
        Apply JSON
      </button>
    </div>
  );
}
