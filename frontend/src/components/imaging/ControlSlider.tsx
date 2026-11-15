/**
 * One image control on a card: a slider with its value and range, an Auto
 * box where the control has an automatic mode, and the error of the last
 * write under it.
 *
 * While the slider is dragged the page shows the dragged value and sends it
 * at most every 150 ms (and always the last one), so the camera follows
 * without flooding the server. The slider is greyed out while Auto is on:
 * the camera chooses the value then, and a manual value would be ignored.
 */

import { useEffect, useRef, useState } from "react";

import {
  controlLabel,
  createThrottle,
  type ImageChange,
  type ImageControlView,
} from "../../lib/imaging";

const DRAG_SEND_INTERVAL_MS = 150;

interface ControlSliderProps {
  cameraId: string;
  name: string;
  control: ImageControlView;
  /** The card is offline: show the last known values, change nothing. */
  disabled: boolean;
  onChange: (name: string, change: ImageChange) => void;
}

export function ControlSlider({ cameraId, name, control, disabled, onChange }: ControlSliderProps) {
  const [dragged, setDragged] = useState<number | null>(null);
  // The latest `onChange`, for a throttle created once per slider.
  const changeRef = useRef(onChange);
  useEffect(() => {
    changeRef.current = onChange;
  }, [onChange]);
  const throttleRef = useRef<{ push: (value: number) => void; cancel: () => void } | null>(null);
  useEffect(() => () => throttleRef.current?.cancel(), []);

  function sendWhileDragging(value: number) {
    throttleRef.current ??= createThrottle<number>(
      (dragValue) => changeRef.current(name, { value: dragValue }),
      DRAG_SEND_INTERVAL_MS,
    );
    throttleRef.current.push(value);
  }

  const label = controlLabel(name);
  const onAuto = control.auto === true;
  const shown = dragged ?? control.value;
  const id = `image-${cameraId}-${name}`;

  return (
    <div className={`image-control${control.error ? " image-control-error" : ""}`}>
      <div className="image-control-head">
        <label htmlFor={id}>{label}</label>
        <span className="image-control-value">{shown}</span>
        <span className="muted image-control-range">
          {control.min}..{control.max}
        </span>
        {control.auto !== null && (
          <label className="image-control-auto">
            <input
              type="checkbox"
              checked={onAuto}
              disabled={disabled}
              aria-label={`${label} auto`}
              // Turning auto off keeps the value the camera chose, so nothing jumps.
              onChange={(event) =>
                onChange(
                  name,
                  event.target.checked ? { auto: true } : { auto: false, value: control.value },
                )
              }
            />
            Auto
          </label>
        )}
      </div>
      <input
        id={id}
        type="range"
        min={control.min}
        max={control.max}
        step={control.step}
        value={shown}
        disabled={disabled || onAuto}
        onChange={(event) => {
          const value = Number(event.target.value);
          setDragged(value);
          sendWhileDragging(value);
        }}
        onPointerUp={() => setDragged(null)}
        onBlur={() => setDragged(null)}
      />
      {control.error && (
        <div className="field-error" role="alert">
          {control.error}
        </div>
      )}
    </div>
  );
}
