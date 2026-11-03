/**
 * Add or edit one camera (WP3 brief §4.1). The fields follow the camera's
 * owner: a studio camera is driven directly (VISCA over IP: host and port);
 * an agent camera names one of an agent's cameras, picked from what the
 * agents module sees live, and may have a direct fallback.
 *
 * "Nudge" and "Stop" try the saved camera through the arbiter like any
 * command, so they also show inversion and scaling at work.
 */

import { useState } from "react";

import type { AgentsState } from "../../lib/agents";
import { emptyAddress, type CameraConfig } from "../../lib/cameras";
import { CheckField, NumberField, SelectField, TextField } from "./fields";

interface CameraFormProps {
  initial: CameraConfig;
  isNew: boolean;
  /** Server validation messages for this camera, keyed like "address.host" ("" for the whole). */
  errors: Record<string, string>;
  agents: AgentsState | undefined;
  /** True when the saved camera is enabled and running, so it can be nudged. */
  canTest: boolean;
  onSave: (camera: CameraConfig) => void;
  onCancel: () => void;
  run: (command: string, args: Record<string, unknown>) => void;
}

export function CameraForm(props: CameraFormProps) {
  const { errors, agents } = props;
  const [draft, setDraft] = useState<CameraConfig>(props.initial);
  const isAgent = draft.owner === "agent";
  const set = (change: Partial<CameraConfig>) => setDraft((current) => ({ ...current, ...change }));
  const setAddress = (change: Partial<CameraConfig["address"]>) =>
    set({ address: { ...draft.address, ...change } });
  const setLimits = (change: Partial<CameraConfig["limits"]>) =>
    set({ limits: { ...draft.limits, ...change } });

  const agentIds = Object.keys(agents?.agents ?? {});
  const agentCameras = agents?.agents[draft.address.agent_id]?.info?.cameras ?? [];
  const fallback = draft.direct_fallback;

  return (
    <form
      className="config-form"
      noValidate
      aria-label={props.isNew ? "New camera" : `Edit ${props.initial.id}`}
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
          help={
            props.isNew ? "Short name, e.g. finish-ptz. It cannot be changed later." : undefined
          }
          error={errors.id}
          onChange={(id) => set({ id })}
        />
        <TextField
          label="Label"
          value={draft.label}
          error={errors.label}
          onChange={(label) => set({ label })}
        />
        <SelectField
          label="Owner"
          value={draft.owner}
          options={[
            { value: "studio", label: "studio (driven directly)" },
            { value: "agent", label: "agent (a trackside laptop owns it)" },
          ]}
          error={errors.owner}
          onChange={(owner) =>
            set(
              owner === "agent"
                ? { owner: "agent", driver: "agent" }
                : { owner: "studio", driver: "visca_ip", direct_fallback: null },
            )
          }
        />
        <SelectField
          label="Driver"
          value={draft.driver}
          options={
            isAgent
              ? [{ value: "agent", label: "agent" }]
              : [{ value: "visca_ip", label: "VISCA over IP" }]
          }
          error={errors.driver}
          onChange={() => undefined}
        />
        {isAgent ? (
          <>
            <SelectField
              label="Agent"
              value={draft.address.agent_id}
              options={agentIds.map((id) => ({ value: id, label: id }))}
              error={errors["address.agent_id"]}
              help="Agents are added on the Agents page."
              onChange={(agent_id) => setAddress({ agent_id, remote_camera_id: "" })}
            />
            <SelectField
              label="Agent's camera"
              value={draft.address.remote_camera_id}
              options={agentCameras.map((camera) => ({
                value: camera.id,
                label: `${camera.label} (${camera.id}, ${camera.kind})`,
              }))}
              error={errors["address.remote_camera_id"]}
              onChange={(remote_camera_id) => setAddress({ remote_camera_id })}
            />
          </>
        ) : (
          <>
            <TextField
              label="Host"
              value={draft.address.host}
              placeholder="192.168.1.188"
              error={errors["address.host"]}
              onChange={(host) => setAddress({ host })}
            />
            <NumberField
              label="Port"
              value={draft.address.port}
              min={1}
              max={65535}
              error={errors["address.port"]}
              onChange={(port) => setAddress({ port: port ?? 52381 })}
            />
          </>
        )}
        <NumberField
          label="Controller address"
          value={draft.controller_address}
          min={1}
          max={255}
          help="The Pelco-D address that selects this camera on the joystick; empty for none."
          error={errors.controller_address}
          onChange={(controller_address) => set({ controller_address })}
        />
        <TextField
          label="OBS source"
          value={draft.obs_source ?? ""}
          help="The OBS source this camera feeds (used from WP6)."
          error={errors.obs_source}
          onChange={(text) => set({ obs_source: text.trim() ? text : null })}
        />
        <SelectField
          label="Speed curve"
          value={draft.speed_curve}
          options={[
            { value: "linear", label: "linear" },
            { value: "squared", label: "squared (finer near the centre)" },
          ]}
          error={errors.speed_curve}
          onChange={(speed_curve) =>
            set({ speed_curve: speed_curve as CameraConfig["speed_curve"] })
          }
        />
      </div>

      <h3>Limits</h3>
      <div className="form-grid">
        {(["pan", "tilt", "zoom"] as const).map((axis) => (
          <NumberField
            key={axis}
            label={`${axis} speed scale`}
            value={draft.limits[`${axis}_speed_scale`]}
            min={0}
            max={1}
            step={0.05}
            error={errors[`limits.${axis}_speed_scale`]}
            onChange={(value) => setLimits({ [`${axis}_speed_scale`]: value ?? 1 })}
          />
        ))}
        <CheckField
          label="Invert pan"
          checked={draft.limits.invert_pan}
          onChange={(invert_pan) => setLimits({ invert_pan })}
        />
        <CheckField
          label="Invert tilt"
          checked={draft.limits.invert_tilt}
          onChange={(invert_tilt) => setLimits({ invert_tilt })}
        />
        <NumberField
          label="Top pan speed step"
          value={draft.limits.pan_speed_max_step}
          min={1}
          max={24}
          help="VISCA step at full deflection (1–24); direct cameras only."
          error={errors["limits.pan_speed_max_step"]}
          onChange={(value) => setLimits({ pan_speed_max_step: value ?? 24 })}
        />
        <NumberField
          label="Top tilt speed step"
          value={draft.limits.tilt_speed_max_step}
          min={1}
          max={23}
          help="VISCA step at full deflection (1–23); direct cameras only."
          error={errors["limits.tilt_speed_max_step"]}
          onChange={(value) => setLimits({ tilt_speed_max_step: value ?? 20 })}
        />
      </div>

      {isAgent && (
        <>
          <h3>Direct fallback</h3>
          <CheckField
            label="Drive the camera directly while its agent is away"
            checked={fallback !== null}
            error={errors.direct_fallback}
            onChange={(on) =>
              set({ direct_fallback: on ? { driver: "visca_ip", address: emptyAddress() } : null })
            }
          />
          {fallback && (
            <div className="form-grid">
              <TextField
                label="Fallback host"
                value={fallback.address.host}
                error={errors["direct_fallback.address.host"]}
                onChange={(host) =>
                  set({ direct_fallback: { ...fallback, address: { ...fallback.address, host } } })
                }
              />
              <NumberField
                label="Fallback port"
                value={fallback.address.port}
                min={1}
                max={65535}
                onChange={(port) =>
                  set({
                    direct_fallback: {
                      ...fallback,
                      address: { ...fallback.address, port: port ?? 52381 },
                    },
                  })
                }
              />
            </div>
          )}
        </>
      )}

      <div className="button-row">
        <button type="submit">{props.isNew ? "Add camera" : "Save"}</button>
        <button type="button" onClick={props.onCancel}>
          Cancel
        </button>
        {props.canTest && (
          <>
            <span className="muted">Test:</span>
            <button type="button" onClick={() => props.run("nudge", { camera: draft.id })}>
              Nudge
            </button>
            <button type="button" onClick={() => props.run("stop", { camera: draft.id })}>
              Stop
            </button>
          </>
        )}
      </div>
    </form>
  );
}
