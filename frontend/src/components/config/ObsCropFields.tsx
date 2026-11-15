/**
 * The part of the camera form for an `obs_crop` camera (WP6b brief §3): the
 * OBS source (picked from OBS's inputs while OBS is connected, typed
 * otherwise), the filter's name, the window's limits and speeds, and, once
 * the camera is saved, "Create filter", "Reset crop" and a live preview.
 *
 * Creating the Crop/Pad filter is always this button: the studio never adds
 * or changes a filter in OBS by itself (ADR 0020).
 */

import { useEffect, useState } from "react";

import type { CameraConfig, CropSettings } from "../../lib/cameras";
import { CameraPreview } from "../cameras/CameraPreview";
import { CheckField, NumberField, SelectField, TextField } from "./fields";

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
  listSources: () => Promise<ObsSourceList>;
  run: (command: string, args: Record<string, unknown>) => void;
}

export function ObsCropFields({
  draft,
  onChange,
  errors,
  saved,
  listSources,
  run,
}: ObsCropFieldsProps) {
  const [sources, setSources] = useState<ObsSourceList | null>(null);
  const crop = draft.crop;
  const setCrop = (change: Partial<CropSettings>) => onChange({ crop: { ...crop, ...change } });

  useEffect(() => {
    let current = true;
    listSources()
      .then((list) => current && setSources(list))
      .catch(() => current && setSources(null));
    return () => {
      current = false;
    };
  }, [listSources]);

  const sourceHelp = sources?.connected
    ? "The OBS input with the 4K camera; its Crop/Pad filter is moved."
    : `OBS is not connected${sources?.reason ? ` (${sources.reason})` : ""}: type the source's name.`;

  return (
    <>
      <h3>OBS crop</h3>
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
