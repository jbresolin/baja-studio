/**
 * The Cameras page's camera list: status, who is moving each camera, its
 * mode and tracker. On a phone it collapses to a dropdown.
 */

import type { CameraView } from "../../lib/cameras";
import { HealthBadge } from "../HealthBadge";

interface CameraListProps {
  ids: string[];
  cameras: Record<string, CameraView>;
  selected: string | null;
  onSelect: (id: string) => void;
  followController: boolean;
  onFollowControllerChange: (follow: boolean) => void;
}

/** "joystick:desk", "web:3fa1…", "agent" or nothing: who drives the camera now. */
export function ownerOf(camera: CameraView): string | null {
  if (camera.control?.velocity_owner) return camera.control.velocity_owner;
  // An agent camera moving on its own is the agent's tracker at work.
  if (camera.owner === "agent" && camera.moving && camera.mode === "auto") return "agent";
  return null;
}

export function CameraBadges({ camera }: { camera: CameraView }) {
  const owner = ownerOf(camera);
  const tracker = camera.tracker?.state;
  return (
    <span className="camera-badges">
      <HealthBadge state={camera.status} />
      {owner && <span className="badge badge-owner">{owner}</span>}
      {camera.owner === "agent" && camera.mode && (
        <span className={`badge badge-mode badge-mode-${camera.mode}`}>{camera.mode}</span>
      )}
      {camera.owner === "agent" && camera.mode?.startsWith("auto") && tracker && (
        <span className="muted">
          {tracker}
          {camera.tracker.target_id && ` ${camera.tracker.target_id}`}
        </span>
      )}
      {camera.route === "fallback" && <span className="badge badge-degraded">direct</span>}
    </span>
  );
}

export function CameraList(props: CameraListProps) {
  const { ids, cameras, selected, onSelect } = props;
  return (
    <nav className="camera-list" aria-label="Cameras">
      <select
        className="camera-select"
        aria-label="Camera"
        value={selected ?? ""}
        onChange={(event) => onSelect(event.target.value)}
      >
        {ids.map((id) => (
          <option key={id} value={id}>
            {cameras[id]!.label} ({cameras[id]!.status})
          </option>
        ))}
      </select>
      <ul>
        {ids.map((id, index) => {
          const camera = cameras[id]!;
          return (
            <li key={id}>
              <button
                className={id === selected ? "camera-entry selected" : "camera-entry"}
                aria-pressed={id === selected}
                data-testid={`camera-${id}`}
                onClick={() => onSelect(id)}
              >
                <span className="camera-entry-title">
                  {index < 9 && <kbd className="muted">{index + 1}</kbd>} {camera.label}
                </span>
                <CameraBadges camera={camera} />
              </button>
            </li>
          );
        })}
      </ul>
      <label className="muted follow-controller">
        <input
          type="checkbox"
          checked={props.followController}
          onChange={(event) => props.onFollowControllerChange(event.target.checked)}
        />{" "}
        Follow controller
      </label>
    </nav>
  );
}
