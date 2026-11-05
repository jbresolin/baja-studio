/**
 * Setup: where the XR18 is, which MIDI device is the M-VAVE and how its
 * faders take over, and whether the D200 actions are on.
 */

import type { AudioSettingsValues } from "../../../lib/audio";
import { CheckField, NumberField, SelectField, TextField } from "../../config/fields";

export type EditSettings = (change: (draft: AudioSettingsValues) => AudioSettingsValues) => void;

export function DevicesSection({
  draft,
  edit,
}: {
  draft: AudioSettingsValues;
  edit: EditSettings;
}) {
  const { mixer, midi, d200 } = draft;
  return (
    <section className="config-form">
      <h3>XR18</h3>
      <div className="form-grid">
        <TextField
          label="Mixer address"
          value={mixer.host}
          placeholder="192.168.1.10"
          help="The XR18's IP address on the studio network."
          onChange={(host) => edit((d) => ({ ...d, mixer: { ...d.mixer, host: host.trim() } }))}
        />
        <NumberField
          label="OSC port"
          value={mixer.port}
          min={1}
          max={65535}
          onChange={(port) => edit((d) => ({ ...d, mixer: { ...d.mixer, port: port ?? 10024 } }))}
        />
        <CheckField
          label="Talk to the mixer"
          checked={mixer.enabled}
          onChange={(enabled) => edit((d) => ({ ...d, mixer: { ...d.mixer, enabled } }))}
        />
      </div>
      <h3>M-VAVE SMC-Mixer</h3>
      <div className="form-grid">
        <TextField
          label="MIDI device name contains"
          value={midi.device_name_match}
          help="The first MIDI port whose name contains this is used."
          onChange={(match) =>
            edit((d) => ({ ...d, midi: { ...d.midi, device_name_match: match } }))
          }
        />
        <SelectField
          label="Fader takeover"
          value={midi.fader_takeover}
          options={[
            { value: "pickup", label: "Pick up (safe: waits for the fader to reach the level)" },
            { value: "jump", label: "Jump (2026: the level jumps to the fader)" },
          ]}
          onChange={(value) =>
            edit((d) => ({
              ...d,
              midi: { ...d.midi, fader_takeover: value as "pickup" | "jump" },
            }))
          }
        />
        <NumberField
          label="Knob step (dB per detent)"
          value={midi.knob_step_db}
          min={0.1}
          max={10}
          step="any"
          onChange={(step) =>
            edit((d) => ({ ...d, midi: { ...d.midi, knob_step_db: step ?? 1.5 } }))
          }
        />
        <CheckField
          label="Use the M-VAVE"
          checked={midi.enabled}
          onChange={(enabled) => edit((d) => ({ ...d, midi: { ...d.midi, enabled } }))}
        />
        <CheckField
          label="Send fader positions back to the device"
          checked={midi.send_fader_feedback}
          onChange={(send) =>
            edit((d) => ({ ...d, midi: { ...d.midi, send_fader_feedback: send } }))
          }
        />
      </div>
      <h3>Ulanzi D200</h3>
      <CheckField
        label="Answer the D200's /action/... requests"
        checked={d200.enabled}
        onChange={(enabled) => edit((d) => ({ ...d, d200: { ...d.d200, enabled } }))}
      />
    </section>
  );
}
