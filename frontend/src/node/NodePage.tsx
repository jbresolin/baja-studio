/**
 * The camera node's own page (ADR 0023): what the node is doing, and its settings.
 *
 * Small on purpose, and usable on a phone at the trackside:
 * - each camera's live status: its video mode, ok/degraded/offline with the
 *   reason, the window's zoom, and the video's figures (frames in and out,
 *   drops, bitrate, encoder) or the OBS link; the laptop's CPU;
 * - the settings (node name, token, port, the cameras: `CameraForm`), saved
 *   with the version they were read at, so two people saving at once see a
 *   conflict instead of losing each other's changes;
 * - the last lines of the node's log.
 *
 * When the node has a token and this tab does not know it, the page asks
 * for it first.
 */

import { useEffect, useState } from "react";

import { HealthBadge } from "../components/HealthBadge";
import { useModuleState } from "../lib/stateHooks";
import { CameraForm } from "./CameraForm";
import {
  getLogTail,
  getNodeSettings,
  type LogEntry,
  MASK,
  newCamera,
  saveNodeSettings,
  saveToken,
  TokenNeededError,
  type NodeCamera,
  type NodeCameraView,
  type NodeSettingsDocument,
  type NodeState,
} from "./nodeApi";

const LOG_LINES = 15;

/** One camera's live status. */
export function CameraStatus({ id, view }: { id: string; view: NodeCameraView }) {
  const pipeline = view.details?.pipeline;
  const obs = view.details?.obs;
  return (
    <div className="card" data-testid={`status-${id}`}>
      <div className="card-header">
        <span className="card-title">{view.label ?? id}</span>
        <span className="badge">{view.mode === "obs" ? "OBS" : "node"}</span>
        <HealthBadge state={view.status ?? "offline"} />
      </div>
      {view.status_reason && <p className="card-message">{view.status_reason}</p>}
      {view.crop && <p>Window: {view.crop.magnification.toFixed(1)}×</p>}
      {pipeline && (
        <p>
          {pipeline.input_fps} fps in, {pipeline.output_fps} fps out, {pipeline.dropped_frames}{" "}
          dropped, {pipeline.bitrate_kbps} kbit/s, {pipeline.encoder || "no encoder yet"}
          {pipeline.restarts > 0 && `, ${pipeline.restarts} restarts`}
          {pipeline.window_apply_ms != null &&
            `, window applied in ${Math.round(pipeline.window_apply_ms)} ms`}
        </p>
      )}
      {view.details?.ffmpeg_log && view.details.ffmpeg_log.length > 0 && (
        <details>
          <summary>ffmpeg&apos;s last lines</summary>
          <pre className="node-ffmpeg-log">{view.details.ffmpeg_log.join("\n")}</pre>
        </details>
      )}
      {view.details?.output && <p>Sends to {view.details.output}</p>}
      {obs && <p>OBS: {obs.connected ? `connected, source ${obs.source ?? "?"}` : obs.problem}</p>}
      {view.last_error && <p className="field-error">{view.last_error}</p>}
    </div>
  );
}

function TokenPrompt({ onToken }: { onToken: (token: string) => void }) {
  const [token, setToken] = useState("");
  return (
    <form
      className="card"
      onSubmit={(event) => {
        event.preventDefault();
        onToken(token);
      }}
    >
      <p>This camera node has a token. Enter it to see and change its settings.</p>
      <input
        type="password"
        aria-label="Node token"
        value={token}
        onChange={(event) => setToken(event.target.value)}
      />
      <button type="submit">Open</button>
    </form>
  );
}

export function NodePage({ onTokenChanged }: { onTokenChanged: () => void }) {
  const state = useModuleState<NodeState>("node");
  const [document, setDocument] = useState<NodeSettingsDocument | null>(null);
  const [needsToken, setNeedsToken] = useState(false);
  const [notice, setNotice] = useState("");
  const [log, setLog] = useState<LogEntry[]>([]);

  // Bumped to read the settings and the log again (after a token was entered).
  const [loadCount, setLoadCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getNodeSettings()
      .then(async (loaded) => {
        if (cancelled) return;
        setDocument(loaded);
        setNeedsToken(false);
        const lines = await getLogTail(LOG_LINES);
        if (!cancelled) setLog(lines);
      })
      .catch((error: Error) => {
        if (cancelled) return;
        if (error instanceof TokenNeededError) setNeedsToken(true);
        else setNotice(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [loadCount]);

  if (needsToken) {
    return (
      <TokenPrompt
        onToken={(token) => {
          saveToken(token);
          onTokenChanged();
          setLoadCount((count) => count + 1);
        }}
      />
    );
  }
  if (!document) return <p>{notice || "Loading…"}</p>;

  const values = document.values;
  const setCameras = (cameras: NodeCamera[]) =>
    setDocument({ ...document, values: { ...values, cameras } });
  const save = async () => {
    try {
      const saved = await saveNodeSettings(values, document.version);
      setDocument(saved);
      setNotice("Saved.");
    } catch (error) {
      setNotice(`Not saved: ${(error as Error).message}`);
    }
  };
  const cpu = state?.system?.cpu_percent;

  return (
    <main className="node-page">
      <h1>Camera node {values.name || values.agent_id}</h1>
      <p>
        Laptop CPU: {cpu === null || cpu === undefined ? "…" : `${cpu}%`} · port {values.port}
      </p>
      {values.token === "" && (
        <p className="field-error">
          No token set: anyone on this network can drive these cameras and change these settings.
        </p>
      )}

      <section className="card-grid">
        {(state?.order ?? []).map((id) => (
          <CameraStatus key={id} id={id} view={state?.cameras[id] ?? {}} />
        ))}
      </section>

      <h2>Settings</h2>
      <form
        className="config-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label className="field">
          Node name
          <input
            value={values.name}
            onChange={(event) =>
              setDocument({ ...document, values: { ...values, name: event.target.value } })
            }
          />
        </label>
        <label className="field">
          Token (the studio&apos;s Agents page needs the same)
          <input
            type="password"
            autoComplete="off"
            value={values.token}
            onChange={(event) =>
              setDocument({ ...document, values: { ...values, token: event.target.value } })
            }
          />
          {values.token === MASK && <span className="field-help">set (hidden)</span>}
        </label>
        <label className="field">
          Port (applies when the node restarts)
          <input
            type="number"
            value={values.port}
            onChange={(event) =>
              setDocument({ ...document, values: { ...values, port: Number(event.target.value) } })
            }
          />
        </label>
        {values.cameras.map((camera, index) => (
          <CameraForm
            key={index}
            camera={camera}
            usbDevices={state?.usb?.devices ?? []}
            onChange={(changed) =>
              setCameras(values.cameras.map((each, at) => (at === index ? changed : each)))
            }
            onRemove={() => setCameras(values.cameras.filter((_, at) => at !== index))}
          />
        ))}
        <div className="button-row">
          <button
            type="button"
            onClick={() => setCameras([...values.cameras, newCamera(values.cameras)])}
          >
            Add camera
          </button>
          <button type="submit">Save</button>
        </div>
        {notice && <p className="card-message">{notice}</p>}
      </form>

      <h2>Log</h2>
      <pre className="node-log">
        {log.map((entry) => `${entry.ts} ${entry.level} ${entry.message}`).join("\n")}
      </pre>
    </main>
  );
}
