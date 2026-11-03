/**
 * Presets of one running camera (WP3 brief §4.3): store the current position
 * in a slot, recall it, and name it. A studio camera's names are saved in
 * the camera registry; an agent camera's on the agent (`PUT /presets/{n}`).
 * The server's `rename_preset` command does either.
 */

import { useState } from "react";

import { presetLabel, type CameraView } from "../../lib/cameras";
import { presetSlots } from "../cameras/PresetPanel";

interface PresetTableProps {
  camera: CameraView;
  run: (command: string, args: Record<string, unknown>) => Promise<unknown>;
}

export function PresetTable({ camera, run }: PresetTableProps) {
  const [names, setNames] = useState<Record<number, string>>({});

  if (camera.capabilities?.preset_max === null) {
    return <p className="muted">This camera has no presets.</p>;
  }

  return (
    <table className="dense-table preset-table">
      <thead>
        <tr>
          <th>Slot</th>
          <th>Name</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {presetSlots(camera).map((n) => {
          const saved = camera.presets.find((preset) => preset.n === n)?.label ?? "";
          const name = names[n] ?? saved;
          return (
            <tr key={n}>
              <td>
                {n + 1} <span className="muted">(#{n})</span>
              </td>
              <td>
                <input
                  aria-label={`Name of preset ${n + 1}`}
                  placeholder={presetLabel(n, [])}
                  maxLength={60}
                  value={name}
                  onChange={(event) => setNames({ ...names, [n]: event.target.value })}
                />
                {name !== saved && (
                  <button
                    onClick={() =>
                      void run("rename_preset", { n, label: name.trim() }).then(() =>
                        setNames((current) => {
                          const next = { ...current };
                          delete next[n];
                          return next;
                        }),
                      )
                    }
                  >
                    Save name
                  </button>
                )}
              </td>
              <td className="nowrap">
                <button onClick={() => void run("store_preset", { n })}>Store here</button>{" "}
                <button onClick={() => void run("recall_preset", { n })}>Recall</button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
