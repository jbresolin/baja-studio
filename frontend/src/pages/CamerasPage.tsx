/**
 * The Cameras page (/cameras): drive any camera from a mouse, a finger or
 * the keyboard, as a peer of the joystick (WP3 brief §3).
 *
 * The page is one more input source, `web:<client id>`, going through the
 * same arbiter as the joystick: hold-to-move (lib/moveStream.ts), a refusal
 * when another source owns the camera, and a Stop that is always obeyed.
 * Which camera is selected belongs to this page only.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { CameraBadges, CameraList } from "../components/cameras/CameraList";
import { CommandTail } from "../components/cameras/CommandTail";
import { ModePanel } from "../components/cameras/ModePanel";
import { PresetPanel } from "../components/cameras/PresetPanel";
import { TouchJoystick } from "../components/TouchJoystick";
import type { CameraKind, CameraPosition } from "../lib/agents";
import { orderedCameraIds, type CamerasState } from "../lib/cameras";
import { useClientId, useModuleState, useSendCommand } from "../lib/stateHooks";
import { useCameraControls } from "../lib/useCameraControls";
import { KEY_LEGEND, SPEED_MAX, SPEED_MIN, SPEED_STEP, type KeyAction } from "../lib/webControls";

const SELECTED_KEY = "baja-studio.selected-camera";
const FOLLOW_KEY = "baja-studio.follow-controller";
const NOTICE_SHOWN_MS = 2500;

function describePosition(kind: CameraKind | undefined, position: CameraPosition): string {
  const zoom = `zoom ${position.zoom.toFixed(2)}`;
  if (kind === "crop")
    return `centre ${position.pan.toFixed(2)}, ${position.tilt.toFixed(2)} ${zoom}`;
  return `pan ${position.pan.toFixed(1)}° tilt ${position.tilt.toFixed(1)}° ${zoom}`;
}

/** The newest camera selection made with a controller's Call button, if any. */
function controllerSelection(
  state: CamerasState | undefined,
): { camera: string; ts: string } | null {
  let newest: { camera: string; ts: string } | null = null;
  for (const controller of Object.values(state?.controllers ?? {})) {
    const selected = controller.selected_camera;
    if (selected && (newest === null || selected.ts > newest.ts)) newest = selected;
  }
  return newest;
}

export function CamerasPage() {
  const state = useModuleState<CamerasState>("cameras");
  const sendCommand = useSendCommand();
  const clientId = useClientId();
  const ids = orderedCameraIds(state);
  const [chosen, setChosen] = useState<string | null>(() => sessionStorage.getItem(SELECTED_KEY));
  const [follow, setFollow] = useState(() => localStorage.getItem(FOLLOW_KEY) === "1");
  const [legendOpen, setLegendOpen] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const selected = chosen !== null && ids.includes(chosen) ? chosen : (ids[0] ?? null);
  const camera = selected ? state?.cameras[selected] : undefined;

  function select(id: string) {
    sessionStorage.setItem(SELECTED_KEY, id);
    setChosen(id);
  }

  // Follow the controller's Call-button selection, when this page asked to.
  // Only a new selection (a new ts) moves the page, so a camera picked here
  // afterwards stays picked. (Adjusting state while rendering, not in an effect.)
  const followed = controllerSelection(state);
  const [followedTs, setFollowedTs] = useState<string | null>(null);
  if (follow && followed && followed.ts !== followedTs) {
    setFollowedTs(followed.ts);
    setChosen(followed.camera);
  }

  useEffect(() => {
    if (notice === null) return;
    const timer = setTimeout(() => setNotice(null), NOTICE_SHOWN_MS);
    return () => clearTimeout(timer);
  }, [notice]);

  const run = async (command: string, args: Record<string, unknown>) => {
    if (!selected) return;
    try {
      const result = await sendCommand("cameras", command, { camera: selected, ...args });
      if (command === "store_preset")
        setNotice({ ok: true, text: `Stored preset ${Number(args.n) + 1}` });
      return result;
    } catch (error) {
      if (command === "set_mode") throw error; // the mode panel shows its own errors
      setNotice({ ok: false, text: (error as Error).message });
    }
  };

  const onKeyAction = (action: KeyAction) => {
    if (action.kind === "recall") void run("recall_preset", { n: action.n });
    else if (action.kind === "store") void run("store_preset", { n: action.n });
    else if (action.kind === "legend") setLegendOpen((open) => !open);
    else if (action.kind === "select" && ids[action.index]) select(ids[action.index]!);
    else if (action.kind === "step-camera" && ids.length > 0) {
      const index = selected ? ids.indexOf(selected) : -1;
      select(ids[(index + action.by + ids.length) % ids.length]!);
    }
  };

  const controls = useCameraControls({
    camera: selected,
    send: (command, args) => sendCommand("cameras", command, args),
    deadband: state?.motion?.joystick_deadband ?? 0,
    zoomVelocity: state?.motion?.joystick_zoom_velocity,
    onKeyAction,
  });

  if (!state) return <p className="muted">Waiting for the studio service…</p>;
  if (ids.length === 0) {
    return (
      <p className="muted">
        No cameras are enabled. Add one on the <Link to="/cameras/config">configuration page</Link>.
      </p>
    );
  }

  const toast = controls.refusal ? { ok: false, text: controls.refusal } : notice;

  return (
    <div className="cameras-page">
      <CameraList
        ids={ids}
        cameras={state.cameras}
        selected={selected}
        onSelect={select}
        followController={follow}
        onFollowControllerChange={(on) => {
          localStorage.setItem(FOLLOW_KEY, on ? "1" : "0");
          setFollow(on);
        }}
      />

      {camera && selected && (
        <section className="camera-control" aria-label={`Control ${camera.label}`}>
          <div className="page-header">
            <h1>{camera.label}</h1>
            <CameraBadges camera={camera} />
            <Link
              className="settings-gear"
              to="/cameras/config"
              aria-label="Camera settings"
              title="Camera settings"
            >
              ⚙
            </Link>
          </div>
          {camera.status_reason && <div className="card-message">{camera.status_reason}</div>}
          <div className="preview-slot">preview — WP6</div>
          <TouchJoystick onChange={controls.setStick} />
          <div className="button-row zoom-row">
            <HoldButton label="Zoom out" onHold={(held) => controls.setZoomButton(held ? -1 : 0)} />
            <HoldButton label="Zoom in" onHold={(held) => controls.setZoomButton(held ? 1 : 0)} />
            <button className="stop-button" onClick={controls.stop}>
              Stop
            </button>
          </div>
          <label className="speed">
            Speed {Math.round(controls.speed * 100)}%
            <input
              type="range"
              aria-label="Speed"
              min={SPEED_MIN}
              max={SPEED_MAX}
              step={SPEED_STEP}
              value={controls.speed}
              onChange={(event) => controls.setSpeed(Number(event.target.value))}
            />
          </label>
          <div className="muted" data-testid="position">
            {camera.position
              ? describePosition(camera.capabilities?.kind, camera.position)
              : "position unknown"}
          </div>
        </section>
      )}

      {camera && selected && (
        <aside className="camera-side">
          <h2>Presets</h2>
          <PresetPanel camera={camera} run={run} />
          {camera.owner === "agent" && (
            <>
              <h2>Mode</h2>
              <ModePanel key={selected} camera={camera} run={run} />
            </>
          )}
          <h2>Recent commands</h2>
          <CommandTail camera={selected} changeMarker={camera.control?.last_command_ts ?? null} />
        </aside>
      )}

      {toast && (
        <div className={toast.ok ? "toast" : "toast toast-error"} role="status">
          {toast.text}
        </div>
      )}
      {legendOpen && (
        <div className="key-legend" role="dialog" aria-label="Keyboard shortcuts">
          <table className="dense-table">
            <tbody>
              {KEY_LEGEND.map(([keys, meaning]) => (
                <tr key={keys}>
                  <td className="nowrap">
                    <kbd>{keys}</kbd>
                  </td>
                  <td>{meaning}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button onClick={() => setLegendOpen(false)}>Close</button>
        </div>
      )}
      <footer className="cameras-footer muted">
        This page is <strong>web:{clientId ?? "…"}</strong> · press <kbd>?</kbd> for keys ·{" "}
        <Link to="/cameras/config">Configure cameras</Link>
      </footer>
    </div>
  );
}

/** A button that reports being held (pointer down) and let go (up, cancel, leaving it). */
function HoldButton({ label, onHold }: { label: string; onHold: (held: boolean) => void }) {
  return (
    <button
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture?.(event.pointerId);
        onHold(true);
      }}
      onPointerUp={() => onHold(false)}
      onPointerCancel={() => onHold(false)}
      onLostPointerCapture={() => onHold(false)}
      onContextMenu={(event) => event.preventDefault()}
    >
      {label}
    </button>
  );
}
