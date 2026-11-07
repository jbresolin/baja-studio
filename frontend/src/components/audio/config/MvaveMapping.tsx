/**
 * Setup: what each M-VAVE control does, strip by strip.
 *
 * Each button is "nothing", "mute", "close/open a send" (on its row's bus
 * or a chosen one) or "run a macro". **MIDI learn**: press Learn, touch a
 * control on the device, and its slot here is highlighted and focused, so
 * nobody has to remember that "S on strip 3" is note 0x0A.
 */

import { useEffect, useRef } from "react";

import {
  AUX_BUSES,
  BUTTON_ROWS,
  CHANNEL_IDS,
  channelLabel,
  POINT_BUSES,
  ROW_NAMES,
  STRIPS,
  type AudioSettingsValues,
  type AudioState,
  type ButtonAction,
  type KnobAction,
} from "../../../lib/audio";
import type { EditSettings } from "./DevicesSection";

/** A button's action as one select value: "mute", "send_toggle:", "send_toggle:bus2", "macro:x". */
export function actionValue(action: ButtonAction | undefined): string {
  if (!action || action.type === "none") return "none";
  if (action.type === "send_toggle") return `send_toggle:${action.bus ?? ""}`;
  if (action.type === "macro") return `macro:${action.macro ?? ""}`;
  return action.type;
}

export function actionFromValue(value: string): ButtonAction {
  const [type, detail] = value.split(":") as [ButtonAction["type"], string | undefined];
  return {
    type,
    channel: null,
    bus: type === "send_toggle" && detail ? detail : null,
    macro: type === "macro" ? (detail ?? null) : null,
  };
}

function knobValue(knob: KnobAction | undefined): string {
  if (!knob || knob.target === "none") return "none";
  return knob.target === "pan" ? "pan" : `aux_send:${knob.bus ?? ""}`;
}

function knobFromValue(value: string): KnobAction {
  if (value === "none" || value === "pan") return { target: value, bus: null };
  const bus = value.split(":")[1];
  return { target: "aux_send", bus: (bus || null) as KnobAction["bus"] };
}

export interface MvaveMappingProps {
  draft: AudioSettingsValues;
  live: AudioState | undefined;
  clientSource: string | null;
  edit: EditSettings;
  run: (command: string, args?: Record<string, unknown>) => void;
}

export function MvaveMapping({ draft, live, clientSource, edit, run }: MvaveMappingProps) {
  const { midi, channels, macros } = draft;
  const learn = live?.midi.learn;
  const cells = useRef<Record<string, HTMLSelectElement | null>>({});
  // Only this page's own learn result highlights a slot.
  const captured = learn?.by === clientSource ? (learn?.captured ?? null) : null;
  const highlighted = captured?.control ?? null;

  useEffect(() => {
    if (highlighted !== null) cells.current[highlighted]?.focus();
  }, [highlighted, captured?.at]);

  const setMidi = (change: Partial<AudioSettingsValues["midi"]>) =>
    edit((d) => ({ ...d, midi: { ...d.midi, ...change } }));

  const channelOptions = CHANNEL_IDS.map((channel) => (
    <option key={channel} value={channel}>
      {channelLabel(channels, channel)}
    </option>
  ));
  const cellClass = (control: string) => (highlighted === control ? "learned" : undefined);

  return (
    <section className="config-form" data-testid="mvave-mapping">
      <div className="button-row">
        {learn?.armed ? (
          <>
            <span className="status-message">Touch a control on the M-VAVE…</span>
            <button type="button" onClick={() => run("midi_learn", { action: "cancel" })}>
              Cancel
            </button>
          </>
        ) : (
          <button type="button" onClick={() => run("midi_learn", { action: "arm" })}>
            Learn: touch a control
          </button>
        )}
        {captured && (
          <span className="muted" data-testid="learned-control">
            You touched {captured.control.replace(/^\w+:/, (kind) => `${kind.slice(0, -1)} `)}
          </span>
        )}
      </div>
      <div className="form-grid">
        {(["solo", "rec", "sel"] as const).map((row) => (
          <label key={row}>
            {ROW_NAMES[row]} buttons close/open the send to{" "}
            <select
              aria-label={`${ROW_NAMES[row]} row bus`}
              value={midi.row_buses[row] ?? "main"}
              onChange={(event) =>
                setMidi({ row_buses: { ...midi.row_buses, [row]: event.target.value } })
              }
            >
              {POINT_BUSES.map((bus) => (
                <option key={bus} value={bus}>
                  {bus === "main" ? "the main mix" : channelLabel(channels, bus)}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <table className="config-list mvave-table">
        <thead>
          <tr>
            <th>Strip</th>
            <th>Fader</th>
            <th>Knob</th>
            {BUTTON_ROWS.map((row) => (
              <th key={row}>{ROW_NAMES[row]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {STRIPS.map((strip) => (
            <tr key={strip}>
              <td>{strip}</td>
              <td className={cellClass(`fader:${strip}`)}>
                <select
                  aria-label={`strip ${strip} fader`}
                  ref={(element) => {
                    cells.current[`fader:${strip}`] = element;
                  }}
                  value={midi.faders[strip] ?? ""}
                  onChange={(event) =>
                    setMidi({ faders: { ...midi.faders, [strip]: event.target.value || null } })
                  }
                >
                  <option value="">nothing</option>
                  {channelOptions}
                </select>
              </td>
              <td className={cellClass(`knob:${strip}`)}>
                <select
                  aria-label={`strip ${strip} knob`}
                  ref={(element) => {
                    cells.current[`knob:${strip}`] = element;
                  }}
                  value={knobValue(midi.knobs[strip])}
                  onChange={(event) =>
                    setMidi({
                      knobs: { ...midi.knobs, [strip]: knobFromValue(event.target.value) },
                    })
                  }
                >
                  <option value="none">nothing</option>
                  <option value="aux_send:">send to the selected bus</option>
                  {AUX_BUSES.map((bus) => (
                    <option key={bus} value={`aux_send:${bus}`}>
                      send to {channelLabel(channels, bus)}
                    </option>
                  ))}
                  <option value="pan">pan</option>
                </select>
              </td>
              {BUTTON_ROWS.map((row) => {
                const key = `${strip}_${row}`;
                return (
                  <td key={row} className={cellClass(`button:${key}`)}>
                    <select
                      aria-label={`strip ${strip} ${ROW_NAMES[row]}`}
                      ref={(element) => {
                        cells.current[`button:${key}`] = element;
                      }}
                      value={actionValue(midi.buttons[key])}
                      onChange={(event) =>
                        setMidi({
                          buttons: { ...midi.buttons, [key]: actionFromValue(event.target.value) },
                        })
                      }
                    >
                      <option value="none">nothing</option>
                      <option value="mute">mute</option>
                      <option value="send_toggle:">close/open (row's bus)</option>
                      {POINT_BUSES.map((bus) => (
                        <option key={bus} value={`send_toggle:${bus}`}>
                          close/open {bus === "main" ? "main mix" : channelLabel(channels, bus)}
                        </option>
                      ))}
                      {macros.map((macro) => (
                        <option key={macro.id} value={`macro:${macro.id}`}>
                          macro: {macro.label || macro.id}
                        </option>
                      ))}
                    </select>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {highlighted?.startsWith("bus:") && (
        <p className="muted">
          That was the bus-select button for {channelLabel(channels, highlighted.slice(4))}; it
          always chooses the knobs' bus.
        </p>
      )}
    </section>
  );
}
