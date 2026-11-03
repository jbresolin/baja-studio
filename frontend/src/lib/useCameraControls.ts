/**
 * The Cameras page's controls for the selected camera, wired to a `MoveStream`.
 *
 * Keeps which keys and buttons are held and where the on-screen stick is,
 * and hands their combined velocity to the stream on every change. Listens to
 * the keyboard on the whole window (except while typing in a text field), and
 * lets go of everything when the tab is hidden or the window loses focus,
 * because the key-ups will not arrive then.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { MoveStream, type SendCommand } from "./moveStream";
import {
  clampSpeed,
  combineControls,
  DEFAULT_ZOOM_VELOCITY,
  HeldKeys,
  isTyping,
  keyAction,
  type ControlInputs,
  type KeyAction,
  NO_INPUT,
} from "./webControls";

const SPEED_KEY = "baja-studio.camera-speed";
const REFUSAL_SHOWN_MS = 2500;

export interface CameraControlsOptions {
  camera: string | null;
  send: SendCommand;
  deadband: number;
  zoomVelocity?: number;
  /** Key presses other than motion: presets, camera selection, legend. */
  onKeyAction: (action: KeyAction) => void;
}

function savedSpeed(): number {
  const saved = Number(localStorage.getItem(SPEED_KEY));
  return saved ? clampSpeed(saved) : 0.5;
}

export function useCameraControls(options: CameraControlsOptions) {
  const { camera, send, deadband, zoomVelocity = DEFAULT_ZOOM_VELOCITY } = options;
  const [speed, setSpeedState] = useState(savedSpeed);
  // `at` makes each refusal a new value, so the toast stays while refusals keep coming.
  const [refusal, setRefusal] = useState<{ text: string; at: number } | null>(null);
  const inputs = useRef<ControlInputs>(NO_INPUT);
  const heldKeys = useRef(new HeldKeys());
  const settings = useRef({ deadband, speed, zoomVelocity });
  const onKeyAction = useRef(options.onKeyAction);
  useLayoutEffect(() => {
    settings.current = { deadband, speed, zoomVelocity };
    onKeyAction.current = options.onKeyAction;
  });

  const [stream] = useState(
    () =>
      new MoveStream({
        send,
        onRefused: (text) => setRefusal({ text, at: Date.now() }),
      }),
  );

  useEffect(() => {
    if (refusal === null) return;
    const timer = setTimeout(() => setRefusal(null), REFUSAL_SHOWN_MS);
    return () => clearTimeout(timer);
  }, [refusal]);

  const push = useCallback(() => {
    stream.update(combineControls(inputs.current, settings.current));
  }, [stream]);

  const setInputs = useCallback(
    (change: Partial<ControlInputs>) => {
      inputs.current = { ...inputs.current, ...change };
      push();
    },
    [push],
  );

  const setSpeed = useCallback(
    (next: number) => {
      const clamped = clampSpeed(next);
      localStorage.setItem(SPEED_KEY, String(clamped));
      settings.current = { ...settings.current, speed: clamped };
      setSpeedState(clamped);
      push();
    },
    [push],
  );

  // A camera switch releases the old camera (MoveStream sends the stop).
  useEffect(() => stream.setCamera(camera), [stream, camera]);

  useEffect(() => {
    function letGoOfEverything() {
      heldKeys.current.clear();
      inputs.current = NO_INPUT;
      stream.release();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (isTyping(event.target)) return;
      if (heldKeys.current.set(event.code, true)) {
        event.preventDefault(); // no page scrolling, no slider nudging
        if (!event.repeat) {
          setInputs({ keys: heldKeys.current.keys(), zoom: heldKeys.current.zoom() });
        }
        return;
      }
      const action = keyAction(event);
      if (action === null) return;
      event.preventDefault();
      if (event.repeat && action.kind !== "speed") return;
      if (action.kind === "stop") stream.stop();
      else if (action.kind === "speed") setSpeed(settings.current.speed + action.by);
      else onKeyAction.current(action);
    }
    function onKeyUp(event: KeyboardEvent) {
      if (event.code === "Space" && !isTyping(event.target)) event.preventDefault();
      if (heldKeys.current.set(event.code, false)) {
        setInputs({ keys: heldKeys.current.keys(), zoom: heldKeys.current.zoom() });
      }
    }
    function onVisibilityChange() {
      if (document.visibilityState === "hidden") letGoOfEverything();
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", letGoOfEverything);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", letGoOfEverything);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      letGoOfEverything();
    };
  }, [stream, setInputs, setSpeed]);

  return {
    speed,
    setSpeed,
    refusal: refusal?.text ?? null,
    setStick: (pan: number, tilt: number) => setInputs({ stick: { pan, tilt } }),
    setZoomButton: (zoom: number) => setInputs({ zoom }),
    stop: () => stream.stop(),
  };
}
