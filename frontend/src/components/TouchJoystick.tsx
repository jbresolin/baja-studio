/**
 * The on-screen joystick: drag the knob with a mouse, a finger or a pen.
 *
 * It reports the knob's deflection as pan (right +) and tilt (up +), each
 * -1..1, clamped to the circle; letting go (or the pointer being taken away
 * by the browser) reports 0, 0. Pointer capture keeps the drag going when
 * the finger slides off the pad.
 */

import { useRef, useState, type PointerEvent } from "react";

interface TouchJoystickProps {
  onChange: (pan: number, tilt: number) => void;
  disabled?: boolean;
}

export function TouchJoystick({ onChange, disabled = false }: TouchJoystickProps) {
  const padRef = useRef<HTMLDivElement>(null);
  const [knob, setKnob] = useState({ pan: 0, tilt: 0 });
  const activePointer = useRef<number | null>(null);

  function deflection(event: PointerEvent<HTMLDivElement>): { pan: number; tilt: number } {
    const rect = padRef.current!.getBoundingClientRect();
    const radius = Math.min(rect.width, rect.height) / 2 || 1;
    let pan = (event.clientX - (rect.left + rect.width / 2)) / radius;
    let tilt = -(event.clientY - (rect.top + rect.height / 2)) / radius;
    const length = Math.hypot(pan, tilt);
    if (length > 1) {
      pan /= length;
      tilt /= length;
    }
    return { pan, tilt };
  }

  function move(event: PointerEvent<HTMLDivElement>) {
    const next = deflection(event);
    setKnob(next);
    onChange(next.pan, next.tilt);
  }

  function release(event: PointerEvent<HTMLDivElement>) {
    if (activePointer.current !== event.pointerId) return;
    activePointer.current = null;
    setKnob({ pan: 0, tilt: 0 });
    onChange(0, 0);
  }

  return (
    <div
      ref={padRef}
      className={disabled ? "joystick-pad disabled" : "joystick-pad"}
      role="application"
      aria-label="Pan and tilt joystick"
      data-testid="joystick"
      onPointerDown={(event) => {
        if (disabled || activePointer.current !== null) return;
        activePointer.current = event.pointerId;
        event.currentTarget.setPointerCapture?.(event.pointerId);
        move(event);
      }}
      onPointerMove={(event) => {
        if (activePointer.current === event.pointerId) move(event);
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
    >
      <div
        className="joystick-knob"
        style={{
          left: `${50 + knob.pan * 40}%`,
          top: `${50 - knob.tilt * 40}%`,
        }}
      />
    </div>
  );
}
