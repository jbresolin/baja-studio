/**
 * Setup: scenes (event phases: a mixer snapshot and/or a macro), and the
 * D200 card: a Ulanzi Studio profile built from the saved settings, and the
 * URLs behind its buttons for this host.
 */

import { useEffect, useState } from "react";

import { getD200MissingMacros } from "../../../lib/api";

import {
  D200_DEFAULT_ADDRESS,
  d200ProfileUrl,
  d200Urls,
  type AudioSettingsValues,
  type SceneConfig,
} from "../../../lib/audio";
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

export function D200Card({
  draft,
  origin,
  dirty,
}: {
  draft: AudioSettingsValues;
  origin: string;
  dirty: boolean;
}) {
  const [address, setAddress] = useState(D200_DEFAULT_ADDRESS);
  // Macros the profile's buttons call that the saved settings lack (asked again after a save).
  const [missing, setMissing] = useState<string[]>([]);
  useEffect(() => {
    if (dirty) return;
    let current = true;
    getD200MissingMacros()
      .then((answer) => current && setMissing(answer.missing))
      .catch(() => current && setMissing([]));
    return () => {
      current = false;
    };
  }, [dirty]);
  return (
    <section className="config-form" data-testid="d200-card">
      <h3>Profile for Ulanzi Studio</h3>
      <p className="muted">
        A profile with every button already set: the seats' microphones and in-ears, the production
        microphones and the macros, as saved here. In Ulanzi Studio, import it (Profiles → Import)
        and pick it for the D200. Download it again after changing seats, production microphones or
        macros (Macros → Restore defaults… adds any button's missing macro).
      </p>
      <label>
        Address the D200 calls
        <input
          value={address}
          onChange={(event) => setAddress(event.target.value)}
          data-testid="d200-address"
        />
      </label>
      <p className="muted">
        <code>{D200_DEFAULT_ADDRESS}</code> when Ulanzi Studio runs on this studio PC; from another
        PC, this page&apos;s address{" "}
        <button type="button" onClick={() => setAddress(origin)}>
          Use {origin}
        </button>
      </p>
      {missing.length > 0 && (
        <p className="status-message" data-testid="d200-missing-macros">
          Buttons whose macro is missing (they answer an error until it exists):{" "}
          {missing.join(", ")}. Macros → Restore defaults… adds them; save, then download.
        </p>
      )}
      {dirty && (
        <p className="status-message">
          Unsaved changes: save first, the profile is built from the saved settings.
        </p>
      )}
      <a
        className="button"
        href={d200ProfileUrl(address)}
        download="Baja Studio.ulanziDeckProfile"
        data-testid="d200-download"
      >
        Download the D200 profile
      </a>
      <h3>The URLs behind the buttons</h3>
      <p className="muted">
        For a button set by hand: the API Request plugin&apos;s &quot;Match Result&quot;, request
        URL below, method GET; status poll every 1 s on the matching state URL; response field{" "}
        <code>muted</code> (mute buttons) or <code>active</code> (macros), expected{" "}
        <code>true</code>.
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
