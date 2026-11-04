/**
 * Add or edit one serial joystick controller (WP3 brief §4.2, §4.3): its
 * port, picked from the ports the service's computer has (with their
 * description and hardware id, so the CP2102N adapter is recognisable), its
 * baud rate, and what each Call button does.
 */

import { useEffect, useState } from "react";

import type { CallAction, CallBinding, ControllerConfig } from "../../lib/cameras";
import { CheckField, Field, SelectField, TextField } from "./fields";

export interface SerialPortChoice {
  device: string;
  description: string;
  hwid: string;
  used_by: string | null;
}

const BAUD_RATES = [2400, 4800, 9600, 19200, 38400, 57600, 115200];
const CALL_BUTTONS = [1, 2, 3, 4, 5, 6, 7, 8, 9];

interface ControllerFormProps {
  initial: ControllerConfig;
  isNew: boolean;
  errors: Record<string, string>;
  cameraIds: string[];
  listPorts: () => Promise<SerialPortChoice[]>;
  onSave: (controller: ControllerConfig) => void;
  onCancel: () => void;
}

export function ControllerForm(props: ControllerFormProps) {
  const { errors, listPorts } = props;
  const [draft, setDraft] = useState<ControllerConfig>(props.initial);
  const [ports, setPorts] = useState<SerialPortChoice[] | null>(null);
  const [portsError, setPortsError] = useState<string | null>(null);
  const [scanCount, setScanCount] = useState(0);
  const set = (change: Partial<ControllerConfig>) =>
    setDraft((current) => ({ ...current, ...change }));

  useEffect(() => {
    listPorts()
      .then((found) => {
        setPorts(found);
        setPortsError(null);
      })
      .catch((error: Error) => setPortsError(error.message));
  }, [listPorts, scanCount]);

  return (
    <form
      className="config-form"
      noValidate
      aria-label={props.isNew ? "New controller" : `Edit ${props.initial.id}`}
      onSubmit={(event) => {
        event.preventDefault();
        props.onSave(draft);
      }}
    >
      {errors[""] && <div className="notice notice-error">{errors[""]}</div>}
      <div className="form-grid">
        <TextField
          label="Id"
          value={draft.id}
          readOnly={!props.isNew}
          error={errors.id}
          onChange={(id) => set({ id })}
        />
        <SelectField
          label="Port"
          value={draft.port}
          options={(ports ?? []).map((port) => ({
            value: port.device,
            label:
              `${port.device} — ${port.description || "unknown"}` +
              (port.used_by && port.used_by !== props.initial.id
                ? ` (used by ${port.used_by})`
                : ""),
          }))}
          help={
            portsError ??
            ports?.find((port) => port.device === draft.port)?.hwid ??
            (ports?.length === 0 ? "No serial ports found." : undefined)
          }
          error={errors.port}
          onChange={(port) => set({ port })}
        />
        <div className="field">
          <button type="button" onClick={() => setScanCount((count) => count + 1)}>
            Look for ports again
          </button>
        </div>
        <SelectField
          label="Baud"
          value={String(draft.baud)}
          options={BAUD_RATES.map((baud) => ({ value: String(baud), label: String(baud) }))}
          error={errors.baud}
          onChange={(baud) => set({ baud: Number(baud) })}
        />
        <SelectField
          label="Protocol"
          value={draft.protocol}
          options={[{ value: "pelco_d", label: "Pelco-D" }]}
          onChange={() => undefined}
        />
        <CheckField
          label="Enabled"
          checked={draft.enabled}
          onChange={(enabled) => set({ enabled })}
        />
      </div>

      <h3>Call buttons</h3>
      <CallBindingsEditor
        bindings={draft.call_bindings}
        cameraIds={props.cameraIds}
        errors={errors}
        onChange={(call_bindings) => set({ call_bindings })}
      />

      <div className="button-row">
        <button type="submit">{props.isNew ? "Add controller" : "Save"}</button>
        <button type="button" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface CallBindingsEditorProps {
  bindings: CallBinding[];
  cameraIds: string[];
  errors: Record<string, string>;
  onChange: (bindings: CallBinding[]) => void;
}

/** One row per Call button 1–9. Only bound buttons are saved. */
export function CallBindingsEditor({
  bindings,
  cameraIds,
  errors,
  onChange,
}: CallBindingsEditorProps) {
  function update(n: number, change: Partial<CallBinding>) {
    const current = bindings.find((binding) => binding.n === n) ?? {
      n,
      action: "none" as CallAction,
      camera: null,
      preset: null,
    };
    const next = { ...current, ...change };
    const others = bindings.filter((binding) => binding.n !== n);
    onChange((next.action === "none" ? others : [...others, next]).sort((a, b) => a.n - b.n));
  }

  return (
    <table className="dense-table call-bindings">
      <tbody>
        {CALL_BUTTONS.map((n) => {
          const binding = bindings.find((candidate) => candidate.n === n);
          const action = binding?.action ?? "none";
          const index = bindings.findIndex((candidate) => candidate.n === n);
          const error = index >= 0 ? errors[`call_bindings.${index}`] : undefined;
          return (
            <tr key={n}>
              <td>Call {n}</td>
              <td>
                <select
                  aria-label={`Call ${n} action`}
                  value={action}
                  onChange={(event) => {
                    const chosen = event.target.value as CallAction;
                    update(n, {
                      action: chosen,
                      camera:
                        chosen === "select_camera"
                          ? (binding?.camera ?? cameraIds[0] ?? null)
                          : null,
                      preset: chosen === "recall_preset" ? (binding?.preset ?? 0) : null,
                    });
                  }}
                >
                  <option value="none">none</option>
                  <option value="select_camera">select camera</option>
                  <option value="recall_preset">recall preset on selected camera</option>
                </select>
              </td>
              <td>
                {action === "select_camera" && (
                  <select
                    aria-label={`Call ${n} camera`}
                    value={binding?.camera ?? ""}
                    onChange={(event) => update(n, { camera: event.target.value })}
                  >
                    {cameraIds.map((id) => (
                      <option key={id} value={id}>
                        {id}
                      </option>
                    ))}
                  </select>
                )}
                {action === "recall_preset" && (
                  <Field label={`Preset for Call ${n}`}>
                    {(id) => (
                      <input
                        id={id}
                        type="number"
                        min={1}
                        max={256}
                        value={(binding?.preset ?? 0) + 1}
                        onChange={(event) =>
                          update(n, { preset: Math.max(0, Number(event.target.value) - 1) })
                        }
                      />
                    )}
                  </Field>
                )}
                {error && <div className="field-error">{error}</div>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
