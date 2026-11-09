/**
 * Edits on-air actions: audio macros to run when a clip starts and when it
 * ends. Used by the playout settings page and by a playlist's override.
 *
 * Only audio macros can be picked here; an action of another kind (added
 * through the advanced JSON) is listed by name and can be removed.
 */

import type { OnAirAction, OnAirActions } from "../../lib/playout";
import { actionMacro, type AudioMacro } from "../../lib/playoutSettings";

const MOMENTS: [keyof OnAirActions, string][] = [
  ["on_clip_start", "When a clip starts"],
  ["on_clip_end", "When a clip ends"],
];

interface OnAirActionsEditorProps {
  value: OnAirActions;
  macros: AudioMacro[];
  onChange: (value: OnAirActions) => void;
}

export function OnAirActionsEditor({ value, macros, onChange }: OnAirActionsEditorProps) {
  const labelOf = (action: OnAirAction): string => {
    const macro = actionMacro(action);
    if (macro === null) return `${action.module}.${action.command}`;
    return `audio macro: ${macros.find((item) => item.id === macro)?.label ?? macro}`;
  };
  return (
    <div className="on-air-actions">
      {MOMENTS.map(([moment, title]) => (
        <div key={moment} className="on-air-moment" data-testid={`on-air-${moment}`}>
          <div className="muted">{title}</div>
          <ul>
            {value[moment].map((action, index) => (
              <li key={index}>
                {labelOf(action)}{" "}
                <button
                  type="button"
                  className="small"
                  aria-label={`Remove ${labelOf(action)}`}
                  onClick={() =>
                    onChange({
                      ...value,
                      [moment]: value[moment].filter((_, position) => position !== index),
                    })
                  }
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
          <select
            aria-label={`Add an audio macro: ${title.toLowerCase()}`}
            value=""
            onChange={(event) => {
              if (!event.target.value) return;
              const action: OnAirAction = {
                module: "audio",
                command: "run_macro",
                args: { macro: event.target.value },
              };
              onChange({ ...value, [moment]: [...value[moment], action] });
            }}
          >
            <option value="">+ audio macro…</option>
            {macros.map((macro) => (
              <option key={macro.id} value={macro.id}>
                {macro.label}
              </option>
            ))}
          </select>
        </div>
      ))}
      {macros.length === 0 && (
        <p className="muted">No audio macros found: is the audio module on and set up?</p>
      )}
    </div>
  );
}
