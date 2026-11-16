/**
 * One camera's settings on the node's page: its name, its USB device (for the
 * image controls), its video mode and that mode's fields.
 *
 * - Node mode: the capture device and its mode (from the node's device list),
 *   the output URL pasted from the studio's Feeds page, encoder, bitrate.
 * - OBS mode: this laptop's OBS (port, password, Test connection), the
 *   camera's source (picked from OBS's list), the filter, Create filter.
 *
 * Secrets the node already has come back as "********"; leaving them so
 * keeps them. Buttons that ask the node something show its answer inline.
 */

import { useState } from "react";

import { MASK, nodeCommand, type NodeCamera, type UsbDeviceRef } from "./nodeApi";

interface Props {
  camera: NodeCamera;
  usbDevices: UsbDeviceRef[];
  onChange: (camera: NodeCamera) => void;
  onRemove: () => void;
}

interface CaptureDevice {
  name: string;
  device: string;
}

interface CaptureMode {
  width: number;
  height: number;
  fps: number;
}

export function CameraForm({ camera, usbDevices, onChange, onRemove }: Props) {
  const [devices, setDevices] = useState<CaptureDevice[]>([]);
  const [modes, setModes] = useState<CaptureMode[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const video = camera.video;
  const obs = camera.obs;
  const set = (changes: Partial<NodeCamera>) => onChange({ ...camera, ...changes });
  const setVideo = (changes: Partial<NodeCamera["video"]>) =>
    set({ video: { ...video, ...changes } });
  const setObs = (changes: Partial<NodeCamera["obs"]>) => set({ obs: { ...obs, ...changes } });

  /** Ask the node something; show its error inline instead of failing. */
  const ask = async (work: () => Promise<string>) => {
    try {
      setMessage(await work());
    } catch (error) {
      setMessage((error as Error).message);
    }
  };

  const listDevices = () =>
    ask(async () => {
      const answer = await nodeCommand<{ devices: CaptureDevice[] }>("list_capture_devices");
      setDevices(answer.devices);
      return `${answer.devices.length} capture device(s)`;
    });

  const listModes = (device: string) =>
    ask(async () => {
      const answer = await nodeCommand<{ modes: CaptureMode[] }>("capture_modes", { device });
      setModes(answer.modes);
      return "";
    });

  const obsArgs = { camera: camera.id, obs: { port: obs.port, password: obs.password } };
  const testObs = () =>
    ask(async () => {
      const answer = await nodeCommand<{ result: string; message: string }>(
        "test_obs_connection",
        obsArgs,
      );
      return `${answer.result}: ${answer.message}`;
    });
  const listSources = () =>
    ask(async () => {
      const answer = await nodeCommand<{
        connected: boolean;
        reason: string;
        sources: { name: string }[];
      }>("list_obs_sources", obsArgs);
      setSources(answer.sources.map((source) => source.name));
      return answer.connected ? `${answer.sources.length} source(s) in OBS` : answer.reason;
    });
  const createFilter = () =>
    ask(async () => {
      const answer = await nodeCommand<{ created: string; source: string }>("create_crop_filter", {
        camera: camera.id,
      });
      return `created "${answer.created}" on ${answer.source}`;
    });

  return (
    <fieldset className="card node-camera-form" data-testid={`camera-form-${camera.id}`}>
      <legend>{camera.label || camera.id}</legend>
      <label className="field">
        Id
        <input value={camera.id} onChange={(event) => set({ id: event.target.value })} />
      </label>
      <label className="field">
        Name
        <input value={camera.label} onChange={(event) => set({ label: event.target.value })} />
      </label>
      <label className="checkbox-chip">
        <input
          type="checkbox"
          checked={camera.enabled}
          onChange={(event) => set({ enabled: event.target.checked })}
        />
        Served to the studio
      </label>
      <label className="field">
        USB device (image controls)
        <select
          value={camera.usb_device?.path ?? ""}
          onChange={(event) => {
            const device = usbDevices.find((each) => each.path === event.target.value) ?? null;
            set({ usb_device: device });
          }}
        >
          <option value="">none</option>
          {usbDevices.map((device) => (
            <option key={device.path} value={device.path}>
              {device.name} #{device.index + 1}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Video done by
        <select
          value={camera.mode}
          onChange={(event) => set({ mode: event.target.value as NodeCamera["mode"] })}
        >
          <option value="node">this node (ffmpeg)</option>
          <option value="obs">OBS on this laptop</option>
        </select>
      </label>

      {camera.mode === "node" ? (
        <div className="node-mode-fields">
          <label className="field">
            Capture device
            <select
              value={video.device}
              onFocus={() => devices.length === 0 && void listDevices()}
              onChange={(event) => {
                const device = devices.find((each) => each.device === event.target.value);
                setVideo({ device: event.target.value, device_name: device?.name ?? "" });
                void listModes(event.target.value);
              }}
            >
              <option value={video.device}>{video.device_name || video.device || "choose…"}</option>
              {devices
                .filter((device) => device.device !== video.device)
                .map((device, index) => (
                  <option key={device.device} value={device.device}>
                    {device.name} #{index + 1}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            Capture mode
            <select
              value={`${video.input_width}x${video.input_height}@${video.input_fps}`}
              onFocus={() => modes.length === 0 && video.device && void listModes(video.device)}
              onChange={(event) => {
                const [size, fps] = event.target.value.split("@");
                const [width, height] = (size ?? "").split("x").map(Number);
                setVideo({
                  input_width: width ?? 3840,
                  input_height: height ?? 2160,
                  input_fps: Number(fps),
                });
              }}
            >
              <option value={`${video.input_width}x${video.input_height}@${video.input_fps}`}>
                {video.input_width}×{video.input_height} at {video.input_fps} fps
              </option>
              {modes.map((mode) => (
                <option
                  key={`${mode.width}x${mode.height}@${mode.fps}`}
                  value={`${mode.width}x${mode.height}@${mode.fps}`}
                >
                  {mode.width}×{mode.height} at {mode.fps} fps
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Output URL (from the studio&apos;s Feeds page)
            <input
              type="password"
              autoComplete="off"
              placeholder="srt://…"
              value={video.output_url}
              onChange={(event) => setVideo({ output_url: event.target.value })}
            />
            {video.output_url === MASK && <span className="field-help">saved (hidden)</span>}
          </label>
          <label className="field">
            Encoder
            <select
              value={video.encoder}
              onChange={(event) => setVideo({ encoder: event.target.value })}
            >
              {["auto", "h264_nvenc", "h264_qsv", "h264_amf", "libx264"].map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Bitrate (kbit/s)
            <input
              type="number"
              value={video.bitrate_kbps}
              onChange={(event) => setVideo({ bitrate_kbps: Number(event.target.value) })}
            />
          </label>
        </div>
      ) : (
        <div className="obs-mode-fields">
          <label className="field">
            OBS WebSocket port
            <input
              type="number"
              value={obs.port}
              onChange={(event) => setObs({ port: Number(event.target.value) })}
            />
          </label>
          <label className="field">
            OBS WebSocket password
            <input
              type="password"
              autoComplete="off"
              value={obs.password}
              onChange={(event) => setObs({ password: event.target.value })}
            />
          </label>
          <div className="button-row">
            <button type="button" onClick={() => void testObs()}>
              Test connection
            </button>
            <button type="button" onClick={() => void listSources()}>
              List OBS sources
            </button>
          </div>
          <label className="field">
            Source in OBS
            <select value={obs.source} onChange={(event) => setObs({ source: event.target.value })}>
              <option value={obs.source}>{obs.source || "choose…"}</option>
              {sources
                .filter((name) => name !== obs.source)
                .map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            Crop filter
            <input
              value={obs.filter_name}
              onChange={(event) => setObs({ filter_name: event.target.value })}
            />
          </label>
          <label className="field">
            Uncropped preview source (optional)
            <input
              value={obs.preview_source}
              onChange={(event) => setObs({ preview_source: event.target.value })}
            />
          </label>
          <button type="button" onClick={() => void createFilter()}>
            Create filter
          </button>
        </div>
      )}
      {message && <p className="card-message">{message}</p>}
      <button type="button" className="danger" onClick={onRemove}>
        Remove camera
      </button>
    </fieldset>
  );
}
