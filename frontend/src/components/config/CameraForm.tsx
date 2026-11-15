/**
 * Add or edit one camera (WP3 brief §4.1). The fields follow the camera's
 * owner: a studio camera is driven directly (VISCA over IP: host and port;
 * a USB PTZ such as the Pixy: driver uvc; no motion at all: driver none; or
 * an OBS crop filter: driver obs_crop, ObsCropFields.tsx), and may name its
 * USB device for image settings (WP6a); an agent camera names one of an
 * agent's cameras, picked from what the agents module sees live, and may
 * have a direct fallback.
 *
 * "Nudge" and "Stop" try the saved camera through the arbiter like any
 * command, so they also show inversion and scaling at work.
 */

import { useCallback, useState } from "react";

import type { AgentsState } from "../../lib/agents";
import {
  emptyAddress,
  type CameraConfig,
  type DriverKind,
  type ObsConnection,
} from "../../lib/cameras";
import type { UsbState } from "../../lib/imaging";
import { CheckField, NumberField, SelectField, TextField } from "./fields";
import type { ObsTestResult } from "./ObsConnectionFields";
import { ObsCropFields, type ObsSourceList } from "./ObsCropFields";
import { UsbDevicePicker } from "./UsbDevicePicker";

const STUDIO_DRIVERS = [
  { value: "visca_ip", label: "VISCA over IP" },
  { value: "uvc", label: "USB PTZ (UVC, e.g. the EMEET Pixy)" },
  { value: "none", label: "none (no motion: a fixed webcam)" },
  { value: "obs_crop", label: "OBS crop (digital PTZ on a 4K webcam)" },
];

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
  /** The UVC child's USB camera list, for the USB device picker. */
  listUsbDevices: () => Promise<UsbState>;
  /** An OBS's inputs, for an OBS crop camera's source (`list_obs_sources {obs, camera}`). */
  listObsSources: (obs: ObsConnection, camera: string) => Promise<ObsSourceList>;
  /** `test_obs_connection {obs, camera}`: can the studio reach that OBS? (WP13) */
  testObsConnection: (obs: ObsConnection, camera: string) => Promise<ObsTestResult>;
  /** What the studio says about the saved camera's OBS connection (its state), if anything. */
  obsConnectionNote?: string;
}

export function CameraForm(props: CameraFormProps) {
  const { errors, agents } = props;
  const [draft, setDraft] = useState<CameraConfig>(props.initial);
  const isAgent = draft.owner === "agent";
  const isObsCrop = draft.driver === "obs_crop";
  const isVisca = draft.driver === "visca_ip";
  const set = (change: Partial<CameraConfig>) => setDraft((current) => ({ ...current, ...change }));
  const setAddress = (change: Partial<CameraConfig["address"]>) =>
    set({ address: { ...draft.address, ...change } });
  const setLimits = (change: Partial<CameraConfig["limits"]>) =>
    set({ limits: { ...draft.limits, ...change } });

  // A masked password ("********") means the one saved with this camera: the
  // server looks it up by the camera's saved id, so that id goes along.
  const savedId = props.isNew ? "" : props.initial.id;
  const { listObsSources, testObsConnection } = props;
  const listSources = useCallback(
    (obs: ObsConnection) => listObsSources(obs, savedId),
    [listObsSources, savedId],
  );
  const testConnection = useCallback(
    (obs: ObsConnection) => testObsConnection(obs, savedId),
    [testObsConnection, savedId],
  );

  const agentIds = Object.keys(agents?.agents ?? {});
  const agentCameras = agents?.agents[draft.address.agent_id]?.info?.cameras ?? [];
  const fallback = draft.direct_fallback;
  // VISCA traffic choked the bench PTZ's video (ADR 0003): its polling is per camera.
  // Shown with the VISCA address, or with the fallback's for an agent camera.
  const positionPollField = (
    <NumberField
      label="Position poll (s)"
      value={draft.position_poll_seconds}
      min={0}
      max={10}
      step={0.25}
      help="Seconds between VISCA position inquiries (0: none); raise it or set 0 if the camera's video stutters. The bench PTZ (192.168.10.245, 2026-11-15) gives no usable position: set 0."
      error={errors.position_poll_seconds}
      onChange={(value) => set({ position_poll_seconds: value ?? 1 })}
    />
  );

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
          options={isAgent ? [{ value: "agent", label: "agent" }] : STUDIO_DRIVERS}
          error={errors.driver}
          onChange={(driver) => {
            if (!isAgent) set({ driver: driver as DriverKind });
          }}
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
        ) : isVisca ? (
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
            {positionPollField}
          </>
        ) : null}
        {!isAgent && (
          <UsbDevicePicker
            cameraId={draft.id}
            value={draft.usb_device}
            required={draft.driver === "uvc"}
            error={errors.usb_device ?? errors["usb_device.path"]}
            listDevices={props.listUsbDevices}
            onChange={(usb_device) => set({ usb_device })}
          />
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
        {!isObsCrop && (
          <TextField
            label="OBS source"
            value={draft.obs_source ?? ""}
            help="The OBS source this camera feeds (its preview on the Cameras page)."
            error={errors.obs_source}
            onChange={(text) => set({ obs_source: text.trim() ? text : null })}
          />
        )}
        <CheckField
          label="Needs presets (the readiness checks warn while it has none)"
          checked={draft.needs_presets}
          error={errors.needs_presets}
          onChange={(needs_presets) => set({ needs_presets })}
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

      {isObsCrop && (
        <ObsCropFields
          draft={draft}
          onChange={set}
          errors={errors}
          saved={props.canTest}
          listSources={listSources}
          testConnection={testConnection}
          connectionNote={props.obsConnectionNote}
          run={props.run}
        />
      )}

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
        {isVisca && (
          <>
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
          </>
        )}
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
              {positionPollField}
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
