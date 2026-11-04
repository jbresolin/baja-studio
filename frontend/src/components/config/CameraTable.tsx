/**
 * The configured cameras in display order: the Cameras page's order and its
 * Alt+1–9 keys. Drag a row, or use its arrows, to reorder. Enabling,
 * disabling, reordering and removing save the whole list at once.
 */

import { useState } from "react";

import type { CameraConfig, CamerasState } from "../../lib/cameras";
import { HealthBadge } from "../HealthBadge";

interface CameraTableProps {
  cameras: CameraConfig[];
  live: CamerasState | undefined;
  onSave: (cameras: CameraConfig[]) => void;
  onEdit: (index: number) => void;
}

export function CameraTable({ cameras, live, onSave, onEdit }: CameraTableProps) {
  const [dragged, setDragged] = useState<number | null>(null);

  function moveCamera(from: number, to: number) {
    if (to < 0 || to >= cameras.length || from === to) return;
    const next = [...cameras];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved!);
    onSave(next);
  }

  return (
    <table className="dense-table config-list" data-testid="camera-list">
      <tbody>
        {cameras.map((camera, index) => {
          const view = live?.cameras[camera.id];
          return (
            <tr
              key={camera.id}
              draggable
              className={dragged === index ? "dragging" : undefined}
              onDragStart={() => setDragged(index)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={() => {
                if (dragged !== null) moveCamera(dragged, index);
                setDragged(null);
              }}
              onDragEnd={() => setDragged(null)}
            >
              <td className="drag-handle" aria-hidden>
                ⋮⋮
              </td>
              <td>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`${camera.id} enabled`}
                    checked={camera.enabled}
                    onChange={(event) =>
                      onSave(
                        cameras.map((c, i) =>
                          i === index ? { ...c, enabled: event.target.checked } : c,
                        ),
                      )
                    }
                  />
                </label>
              </td>
              <td>
                {camera.label || camera.id} <span className="muted">{camera.id}</span>
              </td>
              <td className="muted">
                {camera.owner === "agent"
                  ? `agent ${camera.address.agent_id}/${camera.address.remote_camera_id}`
                  : `${camera.address.host}:${camera.address.port}`}
                {camera.controller_address !== null && ` · addr ${camera.controller_address}`}
              </td>
              <td>
                {view ? <HealthBadge state={view.status} /> : <span className="muted">off</span>}
              </td>
              <td className="nowrap">
                <button
                  aria-label={`Move ${camera.id} up`}
                  onClick={() => moveCamera(index, index - 1)}
                >
                  ↑
                </button>
                <button
                  aria-label={`Move ${camera.id} down`}
                  onClick={() => moveCamera(index, index + 1)}
                >
                  ↓
                </button>
                <button onClick={() => onEdit(index)}>Edit</button>
                <RemoveButton
                  name={camera.id}
                  onRemove={() => onSave(cameras.filter((_, i) => i !== index))}
                />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Remove, after a second click to confirm. */
export function RemoveButton({ name, onRemove }: { name: string; onRemove: () => void }) {
  const [confirming, setConfirming] = useState(false);
  return confirming ? (
    <>
      <button className="danger" onClick={onRemove}>
        Really remove {name}
      </button>
      <button onClick={() => setConfirming(false)}>Keep</button>
    </>
  ) : (
    <button onClick={() => setConfirming(true)}>Remove</button>
  );
}
