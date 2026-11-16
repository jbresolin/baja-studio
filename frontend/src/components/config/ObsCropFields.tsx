/**
 * The part of the camera form for an `obs_crop` camera (WP6b brief §3): which
 * OBS (this PC's or a laptop's, WP13: ObsConnectionFields.tsx), the OBS
 * source (picked from that OBS's inputs while it answers, typed otherwise),
 * the filter's name, the window's limits and speeds, and, once the camera is
 * saved, "Create filter", "Reset crop" and a live preview.
 *
 * Creating the Crop/Pad filter is always this button: the studio never adds
 * or changes a filter in OBS by itself (ADR 0020).
 */

import { useEffect, useState } from "react";

import type { CameraConfig, CropSettings, ObsConnection } from "../../lib/cameras";
import { CameraPreview } from "../cameras/CameraPreview";
import { CheckField, NumberField, SelectField, TextField } from "./fields";
import { ObsConnectionFields, type ObsTestResult } from "./ObsConnectionFields";

export interface ObsSourceList {
  connected: boolean;
  reason: string;
  sources: { name: string; kind: string }[];
}

interface ObsCropFieldsProps {
  draft: CameraConfig;
  onChange: (change: Partial<CameraConfig>) => void;
  errors: Record<string, string>;
  /** True once the camera is saved and running (it can then be tested). */
  saved: boolean;
  /** The inputs of the OBS the form names (`list_obs_sources`). */
  listSources: (obs: ObsConnection) => Promise<ObsSourceList>;
  /** `test_obs_connection` for the OBS the form names. */
  testConnection: (obs: ObsConnection) => Promise<ObsTestResult>;
  /** What the studio says about the saved camera's OBS connection, if anything. */
  connectionNote?: string;
  run: (command: string, args: Record<string, unknown>) => void;
}

export function ObsCropFields({
  draft,
  onChange,
  errors,
  saved,
  listSources,
  testConnection,
  connectionNote,
  run,
}: ObsCropFieldsProps) {
  // The last list, with the OBS it came from (an old one is not shown for another OBS).
  const [listed, setListed] = useState<{ remote: boolean; list: ObsSourceList | null }>({
    remote: false,
    list: null,
  });
  // Bumped to ask the chosen OBS for its inputs again (after a successful test).
  const [listing, setListing] = useState(0);
  const crop = draft.crop;
  const setCrop = (change: Partial<CropSettings>) => onChange({ crop: { ...crop, ...change } });
  const remote = crop.obs.remote;
  const sources = listed.remote === remote ? listed.list : null;

  // The inputs are asked for when the form opens, when "this PC / remote" changes
  // and after Test connection succeeded; not on every keystroke in the address.
  useEffect(() => {
    let current = true;
    listSources(crop.obs)
      .then((list) => current && setListed({ remote, list }))
      .catch(() => current && setListed({ remote, list: null }));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see the comment above
  }, [listSources, remote, listing]);

  const sourceHelp = sources?.connected
    ? "The OBS input with the 4K camera; its Crop/Pad filter is moved."
    : `OBS is not connected${sources?.reason ? ` (${sources.reason})` : ""}: type the source's name.`;

  return (
    <>
      <h3>OBS crop</h3>
      <ObsConnectionFields
        value={crop.obs}
        onChange={(obs) => setCrop({ obs })}
        errors={errors}
        test={testConnection}
        onConnected={() => setListing((count) => count + 1)}
        note={connectionNote}
      />
      <div className="form-grid">
        {sources?.connected ? (
          <SelectField
            label="OBS source"
            value={draft.obs_source ?? ""}
            options={sources.sources.map((source) => ({
              value: source.name,
              label: `${source.name} (${source.kind})`,
            }))}
            help={sourceHelp}
            error={errors.obs_source}
            onChange={(name) => onChange({ obs_source: name || null })}
          />
        ) : (
          <TextField
            label="OBS source"
            value={draft.obs_source ?? ""}
            help={sourceHelp}
            error={errors.obs_source}
            onChange={(text) => onChange({ obs_source: text.trim() ? text : null })}
          />
        )}
        <TextField
          label="Filter name"
          value={crop.filter_name}
          help="The Crop/Pad filter on that source."
          error={errors["crop.filter_name"]}
          onChange={(filter_name) => setCrop({ filter_name })}
        />
        <NumberField
          label="Narrowest window"
          value={crop.min_window}
          min={0.05}
          max={1}
          step={0.05}
          help="Fraction of the source's width: 0.5 on a 4K source is 1080p, no upscaling."
          error={errors["crop.min_window"]}
          onChange={(min_window) => setCrop({ min_window: min_window ?? 0.5 })}
        />
        <TextField
          label="Aspect ratio"
          value={crop.aspect}
          placeholder="16:9"
          help="The window's shape: the output's (16:9)."
          error={errors["crop.aspect"]}
          onChange={(aspect) => setCrop({ aspect })}
        />
        <NumberField
          label="Update rate (Hz)"
          value={crop.update_hz}
          min={5}
          max={60}
          help="How often a moving window is sent to OBS."
          error={errors["crop.update_hz"]}
          onChange={(update_hz) => setCrop({ update_hz: update_hz ?? 25 })}
        />
        {(["pan", "tilt", "zoom"] as const).map((axis) => (
          <NumberField
            key={axis}
            label={`Top ${axis} speed`}
            value={crop[`${axis}_speed`]}
            min={0.01}
            max={2}
            step={0.05}
            help={axis === "zoom" ? "Zoom range per second." : "Frames per second at full stick."}
            error={errors[`crop.${axis}_speed`]}
            onChange={(value) => setCrop({ [`${axis}_speed`]: value ?? 0.3 })}
          />
        ))}
        <CheckField
          label="Pan and tilt slow down as it zooms in (same speed on air at any zoom)"
          checked={crop.speed_follows_zoom}
          error={errors["crop.speed_follows_zoom"]}
          onChange={(speed_follows_zoom) => setCrop({ speed_follows_zoom })}
        />
        <TextField
          label="Preview source (optional)"
          value={crop.preview_source ?? ""}
          help="An OBS source showing this camera uncropped, for a true whole-picture preview."
          error={errors["crop.preview_source"]}
          onChange={(text) => setCrop({ preview_source: text.trim() ? text : null })}
        />
      </div>
      {saved && (
        <>
          <div className="button-row">
            <button type="button" onClick={() => run("create_crop_filter", { camera: draft.id })}>
              Create filter
            </button>
            <button type="button" onClick={() => run("reset_crop", { camera: draft.id })}>
              Reset crop
            </button>
            <span className="muted">Changes to the source and filter apply once saved.</span>
          </div>
          <div className="camera-preview-form">
            <CameraPreview camera={draft.id} width={320} overlay clickToCentre />
          </div>
        </>
      )}
    </>
  );
}
