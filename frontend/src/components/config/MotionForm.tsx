/** The motion settings shared by every camera, each with one line of help (WP3 brief §4.2). */

import { useState } from "react";

import type { MotionSettings } from "../../lib/cameras";
import { NumberField } from "./fields";

interface MotionFormProps {
  initial: MotionSettings;
  errors: Record<string, string>;
  onSave: (motion: MotionSettings) => void;
}

const FIELDS: {
  key: keyof MotionSettings;
  label: string;
  help: string;
  min: number;
  max: number;
  step: number;
}[] = [
  {
    key: "joystick_deadband",
    label: "Joystick deadband",
    help: "Stick deflection treated as rest (0–0.5), for the real and the on-screen joystick.",
    min: 0,
    max: 0.5,
    step: 0.01,
  },
  {
    key: "ttl_ms",
    label: "Velocity ttl (ms)",
    help: "A move not renewed for this long is stopped: the dead-man switch.",
    min: 50,
    max: 2000,
    step: 50,
  },
  {
    key: "resend_interval_ms",
    label: "Resend interval (ms)",
    help: "How often a held velocity is sent again; must be shorter than the ttl.",
    min: 20,
    max: 1000,
    step: 10,
  },
  {
    key: "joystick_zoom_velocity",
    label: "Zoom speed",
    help: "Speed of the controller's and the page's zoom buttons (0–1); they send no speed.",
    min: 0.05,
    max: 1,
    step: 0.05,
  },
  {
    key: "controller_silence_ms",
    label: "Controller silence (ms)",
    help: "Stop the joystick's camera after this long without a frame (0: off; 2000 if ours repeats).",
    min: 0,
    max: 60000,
    step: 100,
  },
];

export function MotionForm({ initial, errors, onSave }: MotionFormProps) {
  const [draft, setDraft] = useState<MotionSettings>(initial);
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  return (
    <form
      className="config-form"
      noValidate
      aria-label="Motion settings"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(draft);
      }}
    >
      {errors[""] && <div className="notice notice-error">{errors[""]}</div>}
      <div className="form-grid">
        {FIELDS.map((field) => (
          <NumberField
            key={field.key}
            label={field.label}
            help={field.help}
            min={field.min}
            max={field.max}
            step={field.step}
            value={draft[field.key]}
            error={errors[field.key]}
            onChange={(value) => setDraft({ ...draft, [field.key]: value ?? 0 })}
          />
        ))}
      </div>
      <div className="button-row">
        <button type="submit" disabled={!changed}>
          Save motion settings
        </button>
      </div>
    </form>
  );
}
