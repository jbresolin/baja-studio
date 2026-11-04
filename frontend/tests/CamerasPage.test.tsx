/**
 * Tests for the Cameras page (src/pages/CamerasPage.tsx and its components).
 *
 * A fake server plays the studio over the fake socket: it sends the cameras
 * module's state, and the tests read the commands the page sent (and answer
 * some of them). Vitest's fake timers stand in for time, so the 100 ms move
 * stream can be counted exactly. Keys are pressed with `fireEvent` on the
 * window, which is where the page listens.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CamerasState, CameraView } from "../src/lib/cameras";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { CamerasPage } from "../src/pages/CamerasPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

function camera(overrides: Partial<CameraView> = {}): CameraView {
  return {
    label: "SMTAV",
    owner: "studio",
    driver: "visca_ip",
    controller_address: 1,
    obs_source: null,
    presets: [{ n: 0, label: "Start gate" }],
    status: "ok",
    status_reason: "",
    route: "direct",
    mode: "manual",
    tracker: { state: "idle", target_id: null, confidence: null },
    position: { pan: 12.5, tilt: -3, zoom: 0.4 },
    moving: false,
    last_error: "",
    capabilities: {
      kind: "ptz",
      has_position_feedback: true,
      has_zoom: true,
      supports_auto: false,
      preset_max: 127,
    },
    control: {
      velocity_owner: null,
      velocity: null,
      latched: false,
      last_source: null,
      last_command: null,
      last_command_ts: null,
    },
    ...overrides,
  };
}

/** Two cameras: the SMTAV (direct) and "trackside", owned by a laptop agent, in auto. */
function twoCameras(): CamerasState {
  return {
    cameras: {
      smtav: camera(),
      trackside: camera({
        label: "Trackside",
        owner: "agent",
        driver: "agent",
        route: "agent",
        mode: "auto",
        moving: true,
        tracker: { state: "tracking", target_id: "car-12", confidence: 0.8 },
        presets: [],
      }),
    },
    controllers: {},
    order: ["smtav", "trackside"],
    motion: { joystick_deadband: 0.1, joystick_zoom_velocity: 0.6 },
  };
}

interface SentCommand {
  module: string;
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

let server: FakeSocket;

/** Render the page connected to a fake server holding `cameras` as its state. */
function showCamerasPage(cameras: CamerasState = twoCameras()) {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <CamerasPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() => server.receive({ type: "snapshot", seq: 1, state: { cameras }, client_id: "desk-pc" }));
}

/** Every command the page sent, in order. */
function sent(): SentCommand[] {
  return server.sent as SentCommand[];
}

/** Answer a command the way the server does. */
function reply(command: SentCommand, outcome: { ok: true } | { ok: false; error: string }) {
  act(() =>
    server.receive({
      type: "reply",
      request_id: command.request_id,
      result: {},
      ...outcome,
    }),
  );
}

function press(code: string, key = code, extra: Record<string, unknown> = {}) {
  act(() => {
    fireEvent.keyDown(window, { code, key, ...extra });
  });
}

function release(code: string, key = code) {
  act(() => {
    fireEvent.keyUp(window, { code, key });
  });
}

function wait(ms: number) {
  act(() => vi.advanceTimersByTime(ms));
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem("baja-studio.camera-speed", "1");
  // The recent-commands panel reads the command log over HTTP.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json([])),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Cameras page", () => {
  it("lists the cameras with their status, mode and tracker, and shows the page's own id", () => {
    showCamerasPage();

    const trackside = screen.getByTestId("camera-trackside");
    expect(within(trackside).getByText("ok")).toHaveClass("badge-ok");
    expect(within(trackside).getByText("auto")).toBeInTheDocument();
    expect(within(trackside).getByText("tracking car-12")).toBeInTheDocument();
    // The agent's tracker is moving it: the owner badge says so.
    expect(within(trackside).getByText("agent")).toHaveClass("badge-owner");
    expect(screen.getByText("web:desk-pc")).toBeInTheDocument();
  });

  it("shows who owns a camera another source is moving", () => {
    const state = twoCameras();
    state.cameras.smtav!.control.velocity_owner = "joystick:desk";

    showCamerasPage(state);

    expect(within(screen.getByTestId("camera-smtav")).getByText("joystick:desk")).toHaveClass(
      "badge-owner",
    );
  });

  it("sends nothing when the page loads or the connection comes back", () => {
    showCamerasPage();
    wait(1000);

    // In particular no stop: on an agent camera a stop would pause auto.
    expect(sent()).toEqual([]);
  });

  it("holding an arrow key sends a move every 100 ms, and letting go sends one stop", () => {
    showCamerasPage();

    press("ArrowRight");
    wait(250);
    release("ArrowRight");
    wait(500);

    const commands = sent().map((command) => command.command);
    expect(commands).toEqual(["move", "move", "move", "stop"]);
    expect(sent()[0]!.args).toEqual({
      camera: "smtav",
      pan: 1,
      tilt: 0,
      zoom: 0,
      ttl_ms: 300,
    });
    expect(sent()[3]!.args).toEqual({ camera: "smtav", release: true });
  });

  it("auto-repeated key presses do not multiply the move stream", () => {
    showCamerasPage();

    press("ArrowUp");
    for (let i = 0; i < 10; i++) press("ArrowUp", "ArrowUp", { repeat: true });
    wait(150);

    expect(sent()).toHaveLength(2); // the press, and one 100 ms later
  });

  it("two arrow keys make one diagonal move", () => {
    showCamerasPage();

    press("ArrowLeft");
    press("ArrowDown");

    expect(sent().at(-1)!.args).toMatchObject({ pan: -1, tilt: -1 });
    wait(100);
    expect(sent().at(-1)!.args).toMatchObject({ pan: -1, tilt: -1 });
  });

  it("applies the speed slider to the keys", () => {
    showCamerasPage();
    fireEvent.change(screen.getByLabelText("Speed"), { target: { value: "0.4" } });

    press("ArrowRight");

    expect(sent().at(-1)!.args).toMatchObject({ pan: 0.4 });
    expect(localStorage.getItem("baja-studio.camera-speed")).toBe("0.4");
  });

  it("switching camera while holding a key stops the old camera first", () => {
    showCamerasPage();
    press("ArrowRight");

    act(() => fireEvent.click(screen.getByTestId("camera-trackside")));
    wait(300);

    expect(sent().map((command) => [command.command, command.args.camera])).toEqual([
      ["move", "smtav"],
      ["stop", "smtav"],
    ]);
  });

  it("stops the camera when the tab is hidden while a key is held", () => {
    showCamerasPage();
    press("ArrowRight");

    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    wait(300);

    expect(sent().map((command) => command.command)).toEqual(["move", "stop"]);
  });

  it("Space and the Stop button send a plain stop", () => {
    showCamerasPage();

    press("Space", " ");
    act(() => fireEvent.click(screen.getByRole("button", { name: "Stop" })));

    expect(sent().map((command) => [command.command, command.args])).toEqual([
      ["stop", { camera: "smtav" }],
      ["stop", { camera: "smtav" }],
    ]);
  });

  it("drives the camera from the on-screen joystick, with the deadband taken out", () => {
    showCamerasPage();
    const pad = screen.getByTestId("joystick");
    // The pad is 200 px wide at (0, 0): its centre is (100, 100), its radius 100.
    pad.getBoundingClientRect = () => DOMRect.fromRect({ x: 0, y: 0, width: 200, height: 200 });

    act(() => {
      fireEvent.pointerDown(pad, { pointerId: 1, clientX: 155, clientY: 100 });
    });
    // 0.55 right, minus the 0.1 deadband, rescaled: 0.5.
    expect(sent().at(-1)!.args).toMatchObject({ camera: "smtav", pan: 0.5, tilt: 0 });

    act(() => {
      fireEvent.pointerUp(pad, { pointerId: 1 });
    });
    expect(sent().at(-1)).toMatchObject({ command: "stop", args: { release: true } });
  });

  it("a stick wobble inside the deadband sends nothing", () => {
    showCamerasPage();
    const pad = screen.getByTestId("joystick");
    pad.getBoundingClientRect = () => DOMRect.fromRect({ x: 0, y: 0, width: 200, height: 200 });

    act(() => {
      fireEvent.pointerDown(pad, { pointerId: 1, clientX: 105, clientY: 97 });
      fireEvent.pointerUp(pad, { pointerId: 1 });
    });

    expect(sent()).toEqual([]);
  });

  it("shows a refused move as a brief notice with the server's reason", async () => {
    showCamerasPage();
    press("ArrowRight");

    reply(sent()[0]!, {
      ok: false,
      error: "camera smtav is being moved by joystick:desk; it takes over once that stops",
    });
    await act(async () => vi.advanceTimersByTimeAsync(0)); // the refused reply settles

    expect(screen.getByRole("status")).toHaveTextContent("being moved by joystick:desk");
    release("ArrowRight");
    wait(3000);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("recalls a preset with a click or its number key, and stores with Shift", () => {
    showCamerasPage();

    act(() => fireEvent.click(screen.getByRole("button", { name: /Start gate/ })));
    press("Digit2", "2");
    act(() =>
      fireEvent.click(screen.getByRole("button", { name: /Preset 3/ }), { shiftKey: true }),
    );
    press("Digit4", "$", { shiftKey: true });

    expect(sent().map((command) => [command.command, command.args.n])).toEqual([
      ["recall_preset", 0],
      ["recall_preset", 1],
      ["store_preset", 2],
      ["store_preset", 3],
    ]);
  });

  it("renames a preset inline", () => {
    showCamerasPage();

    act(() => fireEvent.click(screen.getByRole("button", { name: "Rename preset 2" })));
    const field = screen.getByLabelText("Name of preset 2");
    act(() => fireEvent.change(field, { target: { value: "Jump" } }));
    act(() => fireEvent.submit(field));

    expect(sent().at(-1)).toMatchObject({
      command: "rename_preset",
      args: { camera: "smtav", n: 1, label: "Jump" },
    });
  });

  it("sets an agent camera's mode with its profile, and shows the agent's refusal", async () => {
    showCamerasPage();
    press("BracketRight", "]"); // next camera: trackside
    const zone = screen.getByLabelText("Zone");
    act(() => fireEvent.change(zone, { target: { value: "finish" } }));
    act(() =>
      fireEvent.change(screen.getByLabelText("Priority targets"), {
        target: { value: "car-12, car-7" },
      }),
    );

    act(() => fireEvent.click(screen.getByRole("button", { name: "Auto" })));
    const setMode = sent().at(-1)!;
    expect(setMode).toMatchObject({
      command: "set_mode",
      args: {
        camera: "trackside",
        mode: "auto",
        profile: { resume_after_ms: 3000, zone: "finish", priority_targets: ["car-12", "car-7"] },
      },
    });

    reply(setMode, {
      ok: false,
      error: "the agent refused: tracker_not_ready: no detection model loaded",
    });
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(screen.getByRole("alert")).toHaveTextContent("no detection model loaded");
  });

  it("has no mode panel for a camera the studio drives directly", () => {
    showCamerasPage();

    expect(screen.queryByRole("button", { name: "Auto" })).not.toBeInTheDocument();
  });

  it("Alt+number selects a camera in display order", () => {
    showCamerasPage();

    press("Digit2", "2", { altKey: true });

    expect(screen.getByTestId("camera-trackside")).toHaveAttribute("aria-pressed", "true");
  });

  it("follows the controller's Call-button selection when asked to", () => {
    showCamerasPage();
    act(() => fireEvent.click(screen.getByLabelText("Follow controller")));

    act(() =>
      server.receive({
        type: "patch",
        module: "cameras",
        path: "controllers.desk",
        seq: 2,
        value: {
          port: "COM8",
          baud: 2400,
          status: "ok",
          status_reason: "",
          last_call: { n: 2, ts: "2026-11-03T12:00:00Z" },
          selected_camera: { camera: "trackside", ts: "2026-11-03T12:00:00Z" },
          recent_frames: [],
        },
      }),
    );

    expect(screen.getByTestId("camera-trackside")).toHaveAttribute("aria-pressed", "true");
  });
});
