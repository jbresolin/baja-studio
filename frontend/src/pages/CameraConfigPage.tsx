/**
 * The camera configuration page (/cameras/config, WP3 brief §4): cameras,
 * controllers, motion settings, presets and Call bindings, all saved into the
 * cameras module's settings and applied live, so nothing needs a code change
 * at the event.
 *
 * Enabling, disabling, reordering and removing a camera save at once
 * (components/config/CameraTable.tsx); the forms save on Save.
 */

import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import { CameraForm } from "../components/config/CameraForm";
import { CameraTable, RemoveButton } from "../components/config/CameraTable";
import { ControllerForm, type SerialPortChoice } from "../components/config/ControllerForm";
import { FrameMonitor } from "../components/config/FrameMonitor";
import { MotionForm } from "../components/config/MotionForm";
import { PresetTable } from "../components/config/PresetTable";
import { HealthBadge } from "../components/HealthBadge";
import type { AgentsState } from "../lib/agents";
import {
  newCamera,
  newController,
  type CameraConfig,
  type CamerasState,
  type ControllerConfig,
} from "../lib/cameras";
import { useModuleState, useSendCommand } from "../lib/stateHooks";
import { errorsUnder, useCamerasSettings } from "../lib/useCamerasSettings";

type Editing = { kind: "camera" | "controller"; index: number | "new" } | null;

export function CameraConfigPage() {
  const settings = useCamerasSettings();
  const live = useModuleState<CamerasState>("cameras");
  const agents = useModuleState<AgentsState>("agents");
  const sendCommand = useSendCommand();
  const [editing, setEditing] = useState<Editing>(null);
  const [commandError, setCommandError] = useState<string | null>(null);
  const { values, fieldErrors } = settings;

  const run = useCallback(
    async (command: string, args: Record<string, unknown>) => {
      setCommandError(null);
      try {
        const result = await sendCommand("cameras", command, args);
        // A studio camera's preset name is saved in these settings: read them again.
        if (command === "rename_preset") settings.reload();
        return result;
      } catch (error) {
        setCommandError(`${command}: ${(error as Error).message}`);
      }
    },
    [sendCommand, settings],
  );

  const listPorts = useCallback(async () => {
    const reply = (await sendCommand("cameras", "list_serial_ports")) as {
      ports: SerialPortChoice[];
    };
    return reply.ports;
  }, [sendCommand]);

  if (!values) {
    return settings.notice ? (
      <div className="notice notice-error">{settings.notice.text}</div>
    ) : (
      <p className="muted">Loading…</p>
    );
  }

  const cameras = values.cameras;
  const controllers = values.controllers;

  async function saveCameras(next: CameraConfig[]) {
    if (await settings.save({ cameras: next })) setEditing(null);
  }

  async function saveControllers(next: ControllerConfig[]) {
    if (await settings.save({ controllers: next })) setEditing(null);
  }

  function saveCamera(index: number | "new", draft: CameraConfig) {
    if (index === "new") {
      void saveCameras([...cameras, draft]);
      return;
    }
    // Preset names are edited in the preset table (and may have changed
    // since this form opened), so the saved ones are kept.
    const kept = { ...draft, presets: cameras[index]!.presets };
    void saveCameras(cameras.map((camera, i) => (i === index ? kept : camera)));
  }

  function saveController(index: number | "new", draft: ControllerConfig) {
    void saveControllers(
      index === "new"
        ? [...controllers, draft]
        : controllers.map((controller, i) => (i === index ? draft : controller)),
    );
  }

  const notice = settings.notice;
  const editingCamera = editing?.kind === "camera" ? editing.index : null;
  const editingController = editing?.kind === "controller" ? editing.index : null;

  return (
    <div className="config-page">
      <div className="page-header">
        <h1>Camera setup</h1>
        <Link to="/cameras">Cameras</Link>
        <span className="muted">settings version {settings.version}</span>
      </div>
      {notice && (
        <div className={notice.kind === "saved" ? "notice" : "notice notice-error"} role="status">
          {notice.text}
          {notice.kind === "conflict" && (
            <button type="button" onClick={settings.reload}>
              Load latest
            </button>
          )}
        </div>
      )}
      {commandError && (
        <div className="notice notice-error" role="alert">
          {commandError}
        </div>
      )}
      {errorsUnder(fieldErrors, "cameras")[""] && (
        <div className="notice notice-error">{errorsUnder(fieldErrors, "cameras")[""]}</div>
      )}

      <h2>Cameras</h2>
      <CameraTable
        cameras={cameras}
        live={live}
        onSave={(next) => void saveCameras(next)}
        onEdit={(index) => setEditing({ kind: "camera", index })}
      />
      {editingCamera === null && (
        <div className="button-row">
          <button onClick={() => setEditing({ kind: "camera", index: "new" })}>Add camera</button>
        </div>
      )}
      {editingCamera !== null && (
        <CameraForm
          key={String(editingCamera)}
          initial={editingCamera === "new" ? newCamera("") : cameras[editingCamera]!}
          isNew={editingCamera === "new"}
          errors={errorsUnder(
            fieldErrors,
            `cameras.${editingCamera === "new" ? cameras.length : editingCamera}`,
          )}
          agents={agents}
          canTest={
            editingCamera !== "new" && live?.cameras[cameras[editingCamera]!.id] !== undefined
          }
          onSave={(draft) => saveCamera(editingCamera, draft)}
          onCancel={() => setEditing(null)}
          run={(command, args) => void run(command, args)}
        />
      )}
      {editingCamera !== null &&
        editingCamera !== "new" &&
        live?.cameras[cameras[editingCamera]!.id] && (
          <>
            <h3>Presets</h3>
            <PresetTable
              camera={live.cameras[cameras[editingCamera]!.id]!}
              run={(command, args) => run(command, { camera: cameras[editingCamera]!.id, ...args })}
            />
          </>
        )}

      <h2>Controllers</h2>
      {controllers.length === 0 && <p className="muted">No joystick controllers yet.</p>}
      {controllers.map((controller, index) => {
        const view = live?.controllers[controller.id];
        return (
          <section
            key={controller.id}
            className="agent-card"
            data-testid={`controller-${controller.id}`}
          >
            <div className="card-header">
              <span className="card-title">
                {controller.id}{" "}
                <span className="muted">
                  {controller.port} · {controller.baud} baud
                </span>
              </span>
              {view ? <HealthBadge state={view.status} /> : <span className="muted">disabled</span>}
            </div>
            {view?.status_reason && <div className="card-message">{view.status_reason}</div>}
            {view && (
              <FrameMonitor
                controllerId={controller.id}
                frames={view.recent_frames ?? []}
                readRawFrames={async () => {
                  const reply = (await sendCommand("cameras", "raw_frames", {
                    controller: controller.id,
                  })) as { frames: string[] };
                  return reply.frames;
                }}
              />
            )}
            <div className="button-row">
              <button onClick={() => setEditing({ kind: "controller", index })}>Edit</button>
              <RemoveButton
                name={controller.id}
                onRemove={() => void saveControllers(controllers.filter((_, i) => i !== index))}
              />
            </div>
            {editingController === index && (
              <ControllerForm
                initial={controller}
                isNew={false}
                errors={errorsUnder(fieldErrors, `controllers.${index}`)}
                cameraIds={cameras.map((camera) => camera.id)}
                listPorts={listPorts}
                onSave={(draft) => saveController(index, draft)}
                onCancel={() => setEditing(null)}
              />
            )}
          </section>
        );
      })}
      {editingController === "new" ? (
        <ControllerForm
          initial={newController("")}
          isNew
          errors={errorsUnder(fieldErrors, `controllers.${controllers.length}`)}
          cameraIds={cameras.map((camera) => camera.id)}
          listPorts={listPorts}
          onSave={(draft) => saveController("new", draft)}
          onCancel={() => setEditing(null)}
        />
      ) : (
        <div className="button-row">
          <button onClick={() => setEditing({ kind: "controller", index: "new" })}>
            Add controller
          </button>
        </div>
      )}

      <h2>Motion</h2>
      <MotionForm
        key={settings.version ?? 0}
        initial={values.motion}
        errors={errorsUnder(fieldErrors, "motion")}
        onSave={(motion) => void settings.save({ motion })}
      />
    </div>
  );
}
