/**
 * Setup: scenes (event phases: a mixer snapshot and/or a macro), and the
 * D200 reference card: the URLs to paste into Ulanzi Studio for this host.
 */

import { d200Urls, type AudioSettingsValues, type SceneConfig } from "../../../lib/audio";
import type { EditSettings } from "./DevicesSection";

export function SceneTable({ draft, edit }: { draft: AudioSettingsValues; edit: EditSettings }) {
  const update = (index: number, change: Partial<SceneConfig>) =>
    edit((d) => ({
      ...d,
      scenes: d.scenes.map((s, i) => (i === index ? { ...s, ...change } : s)),
    }));

  return (
    <section className="config-form">
      <p className="muted">
        A scene that loads a mixer snapshot replaces the whole mix; the audio page asks before
        recalling it.
      </p>
      <table className="config-list" data-testid="scene-table">
        <thead>
          <tr>
            <th>Number</th>
            <th>Label</th>
            <th>Mixer snapshot</th>
            <th>Macro</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.scenes.map((scene, index) => (
            <tr key={index}>
              <td>
                <input
                  type="number"
                  aria-label={`scene ${index + 1} number`}
                  min={1}
                  max={64}
                  value={scene.index}
                  onChange={(event) => update(index, { index: Number(event.target.value) })}
                />
              </td>
              <td>
                <input
                  aria-label={`scene ${index + 1} label`}
                  value={scene.label}
                  onChange={(event) => update(index, { label: event.target.value })}
                />
              </td>
              <td>
                <input
                  type="number"
                  aria-label={`scene ${index + 1} snapshot`}
                  min={1}
                  max={64}
                  value={scene.snapshot ?? ""}
                  onChange={(event) =>
                    update(index, {
                      snapshot: event.target.value ? Number(event.target.value) : null,
                    })
                  }
                />
              </td>
              <td>
                <select
                  aria-label={`scene ${index + 1} macro`}
                  value={scene.macro ?? ""}
                  onChange={(event) => update(index, { macro: event.target.value || null })}
                >
                  <option value="">none</option>
                  {draft.macros.map((macro) => (
                    <option key={macro.id} value={macro.id}>
                      {macro.label || macro.id}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <button
                  type="button"
                  onClick={() =>
                    edit((d) => ({ ...d, scenes: d.scenes.filter((_, i) => i !== index) }))
                  }
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="button-row">
        <button
          type="button"
          onClick={() =>
            edit((d) => {
              const next = Math.max(0, ...d.scenes.map((s) => s.index)) + 1;
              return {
                ...d,
                scenes: [...d.scenes, { index: next, label: "", snapshot: next, macro: null }],
              };
            })
          }
        >
          Add scene
        </button>
      </div>
    </section>
  );
}

export function D200Card({ draft, origin }: { draft: AudioSettingsValues; origin: string }) {
  return (
    <section className="config-form" data-testid="d200-card">
      <p className="muted">
        In Ulanzi Studio use the API Request plugin's "Match Result": request URL below, method GET;
        status poll every 1 s on the matching state URL; response field <code>muted</code> (mute
        buttons) or <code>active</code> (macros), expected <code>true</code>. The 2026 profile only
        needs <code>http://localhost:3000</code> replaced with <code>{origin}</code>.
      </p>
      <table className="config-list">
        <tbody>
          {d200Urls(
            origin,
            draft.macros.map((macro) => macro.id),
          ).map(({ label, url }) => (
            <tr key={url}>
              <td>{label}</td>
              <td className="mono">{url}</td>
              <td>
                <button type="button" onClick={() => void navigator.clipboard?.writeText(url)}>
                  Copy
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
