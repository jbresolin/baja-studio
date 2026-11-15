/**
 * One image control on a card: a slider with its value and range, an Auto
 * box where the control has an automatic mode, and the error of the last
 * write under it.
 *
 * While the slider is dragged the page shows the dragged value and keeps one
 * write in flight for this control: the next goes when the reply to the last
 * comes, with the latest value (and the last one always goes). So the camera
 * follows as fast as it answers, without flooding the server. The slider is greyed out while Auto is on:
 * the camera chooses the value then, and a manual value would be ignored.
 */

import { useEffect, useRef, useState } from "react";

import {
  controlLabel,
  createLatestSender,
  type ImageChange,
  type ImageControlView,
} from "../../lib/imaging";

interface ControlSliderProps {
  cameraId: string;
  name: string;
  control: ImageControlView;
  /** The card is offline: show the last known values, change nothing. */
  disabled: boolean;
  /** Resolves when the server has answered (refusals included). */
  onChange: (name: string, change: ImageChange) => Promise<void>;
}

export function ControlSlider({ cameraId, name, control, disabled, onChange }: ControlSliderProps) {
  const [dragged, setDragged] = useState<number | null>(null);
  // The latest `onChange`, for a sender created once per slider.
  const changeRef = useRef(onChange);
  useEffect(() => {
    changeRef.current = onChange;
  }, [onChange]);
  const senderRef = useRef<{ push: (value: number) => void; cancel: () => void } | null>(null);
  useEffect(() => () => senderRef.current?.cancel(), []);

  function sendWhileDragging(value: number) {
    senderRef.current ??= createLatestSender<number>((dragValue) =>
      changeRef.current(name, { value: dragValue }),
    );
    senderRef.current.push(value);
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
                void onChange(
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
