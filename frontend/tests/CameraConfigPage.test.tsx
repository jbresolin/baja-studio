/**
 * Tests for the camera configuration page (src/pages/CameraConfigPage.tsx
 * and src/components/config/).
 *
 * The page reads and saves the cameras settings over HTTP (`fetch`, faked
 * here by a tiny in-memory server that records every save), and reads live
 * state and sends commands over the WebSocket (a fake socket the test plays).
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentsState } from "../src/lib/agents";
import { newCamera, type CamerasSettingsValues } from "../src/lib/cameras";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { CameraConfigPage } from "../src/pages/CameraConfigPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

function savedSettings(): CamerasSettingsValues {
  return {
    enabled: true,
    cameras: [
      {
        ...newCamera("smtav"),
        label: "SMTAV",
        address: { ...newCamera("").address, host: "10.0.0.9" },
      },
      { ...newCamera("wide"), address: { ...newCamera("").address, host: "10.0.0.10" } },
    ],
    controllers: [
      {
        id: "desk",
        port: "COM8",
        baud: 2400,
        protocol: "pelco_d",
        enabled: true,
        call_bindings: [],
      },
    ],
    motion: {
      joystick_deadband: 0.04,
      ttl_ms: 300,
      resend_interval_ms: 100,
      joystick_zoom_velocity: 0.6,
      controller_silence_ms: 0,
    },
  };
}

const AGENTS: AgentsState = {
  agents: {
    "laptop-a": {
      host: "10.0.0.5",
      port: 8890,
      enabled: true,
      status: "ok",
      reason: "",
      info: {
        agent_id: "laptop-a",
        name: "Trackside laptop",
        contract_version: "1.0",
        software_version: "autoptz 0.3",
        cameras: [{ id: "cam1", label: "Finish line", kind: "ptz", driver: "visca" }],
      },
      cameras: {},
      events: [],
      last_seen: null,
    },
  },
  scan: { running: false, cidr: "", found: [], error: "" },
};

/** What the fake HTTP server answers to the next PUT, instead of saving. */
let nextSaveAnswer: Response | null;
let saves: { version: number; values: Partial<CamerasSettingsValues> }[];
let server: FakeSocket;

beforeEach(() => {
  nextSaveAnswer = null;
  saves = [];
  let document = { section: "cameras", version: 7, updated_at: "", values: savedSettings() };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path !== "/api/settings/cameras") return new Response("{}", { status: 404 });
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        saves.push(body);
        if (nextSaveAnswer) return nextSaveAnswer;
        document = {
          ...document,
          version: document.version + 1,
          values: { ...document.values, ...body.values },
        };
      }
      return Response.json(document);
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

interface SentCommand {
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

async function showConfigPage() {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <CameraConfigPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() =>
    server.receive({
      type: "snapshot",
      seq: 1,
      state: {
        agents: AGENTS,
        cameras: {
          order: ["smtav", "wide"],
          motion: {},
          cameras: {
            smtav: {
              label: "SMTAV",
              status: "ok",
              presets: [{ n: 0, label: "Start gate" }],
              capabilities: { preset_max: 127 },
              control: {},
            },
          },
          controllers: {
            desk: {
              port: "COM8",
              baud: 2400,
              status: "ok",
              status_reason: "",
              last_call: null,
              selected_camera: null,
              recent_frames: [
                {
                  ts: "2026-11-03T12:00:00Z",
                  hex: "FF 01 00 02 3F 00 42",
                  action: "move",
                  address: 1,
                  pan: 1,
                  tilt: 0,
                  zoom: 0,
                },
              ],
            },
          },
        },
      },
    }),
  );
  await screen.findByTestId("camera-list");
}

function lastCommand(): SentCommand {
  return server.sent.at(-1) as SentCommand;
}

/** Answer the last command with `result`. */
function answer(result: unknown) {
  const command = lastCommand();
  act(() => server.receive({ type: "reply", request_id: command.request_id, ok: true, result }));
}

describe("Camera configuration page", () => {
  it("lists the cameras in display order with their live status", async () => {
    await showConfigPage();

    const rows = within(screen.getByTestId("camera-list")).getAllByRole("row");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("smtav"),
      expect.stringContaining("wide"),
    ]);
    expect(within(rows[0]!).getByText("ok")).toHaveClass("badge-ok");
    expect(within(rows[1]!).getByText("off")).toBeInTheDocument();
  });

  it("disabling a camera saves at once, from the version it was loaded at", async () => {
    await showConfigPage();

    fireEvent.click(screen.getByLabelText("wide enabled"));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.version).toBe(7);
    expect(saves[0]!.values.cameras!.map((camera) => camera.enabled)).toEqual([true, false]);
  });

  it("moving a camera down saves the new order", async () => {
    await showConfigPage();

    fireEvent.click(screen.getByRole("button", { name: "Move smtav down" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.values.cameras!.map((camera) => camera.id)).toEqual(["wide", "smtav"]);
  });

  it("a studio camera asks for a host; an agent camera for one of an agent's cameras", async () => {
    await showConfigPage();
    fireEvent.click(screen.getByRole("button", { name: "Add camera" }));
    const form = screen.getByRole("form", { name: "New camera" });

    expect(within(form).getByLabelText("Host")).toBeInTheDocument();
    expect(within(form).queryByLabelText("Agent")).not.toBeInTheDocument();

    fireEvent.change(within(form).getByLabelText("Owner"), { target: { value: "agent" } });

    expect(within(form).queryByLabelText("Host")).not.toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("Agent"), { target: { value: "laptop-a" } });
    const agentCamera = within(form).getByLabelText("Agent's camera");
    expect(within(agentCamera).getByText("Finish line (cam1, ptz)")).toBeInTheDocument();
    // Only an agent camera can have a direct fallback.
    fireEvent.click(within(form).getByLabelText(/Drive the camera directly/));
    expect(within(form).getByLabelText("Fallback host")).toBeInTheDocument();
  });

  it("adds a camera and saves it at the end of the list", async () => {
    await showConfigPage();
    fireEvent.click(screen.getByRole("button", { name: "Add camera" }));
    const form = screen.getByRole("form", { name: "New camera" });

    fireEvent.change(within(form).getByLabelText("Id"), { target: { value: "finish" } });
    fireEvent.change(within(form).getByLabelText("Host"), { target: { value: "10.0.0.11" } });
    fireEvent.change(within(form).getByLabelText("Controller address"), { target: { value: "3" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add camera" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    const added = saves[0]!.values.cameras!.at(-1)!;
    expect(added).toMatchObject({
      id: "finish",
      owner: "studio",
      driver: "visca_ip",
      address: { host: "10.0.0.11", port: 52381 },
      controller_address: 3,
    });
  });

  it("shows the server's validation message next to the field it is about", async () => {
    await showConfigPage();
    nextSaveAnswer = Response.json(
      {
        detail: [
          {
            loc: ["cameras", 1, "address", "host"],
            msg: "String should have at most 253 characters",
          },
        ],
      },
      { status: 422 },
    );
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]!);
    const form = screen.getByRole("form", { name: "Edit wide" });

    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    expect(await within(form).findByText("String should have at most 253 characters")).toHaveClass(
      "field-error",
    );
    expect(screen.getByRole("form", { name: "Edit wide" })).toBeInTheDocument(); // still open
  });

  it("says so when someone else saved first, and offers to load the latest", async () => {
    await showConfigPage();
    nextSaveAnswer = Response.json(
      { detail: { message: "stale", current_version: 9 } },
      { status: 409 },
    );

    fireEvent.click(screen.getByLabelText("wide enabled"));

    expect(await screen.findByText(/now version 9/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load latest" })).toBeInTheDocument();
  });

  it("nudges and stops a running camera from its form", async () => {
    await showConfigPage();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);

    fireEvent.click(screen.getByRole("button", { name: "Nudge" }));
    expect(lastCommand()).toMatchObject({ command: "nudge", args: { camera: "smtav" } });
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(lastCommand()).toMatchObject({ command: "stop", args: { camera: "smtav" } });
  });

  it("stores, recalls and names presets of a running camera", async () => {
    await showConfigPage();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);

    fireEvent.click(screen.getAllByRole("button", { name: "Store here" })[1]!);
    expect(lastCommand()).toMatchObject({
      command: "store_preset",
      args: { camera: "smtav", n: 1 },
    });
    fireEvent.change(screen.getByLabelText("Name of preset 2"), { target: { value: "Jump" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));

    expect(lastCommand()).toMatchObject({
      command: "rename_preset",
      args: { camera: "smtav", n: 1, label: "Jump" },
    });
  });

  it("offers the computer's serial ports with what they are", async () => {
    await showConfigPage();
    fireEvent.click(
      within(screen.getByTestId("controller-desk")).getByRole("button", { name: "Edit" }),
    );

    expect(lastCommand().command).toBe("list_serial_ports");
    answer({
      ports: [
        { device: "COM3", description: "Intel AMT", hwid: "PCI", used_by: null },
        {
          device: "COM8",
          description: "Silicon Labs CP210x",
          hwid: "USB VID:PID=10C4:EA60",
          used_by: "desk",
        },
      ],
    });

    const form = screen.getByRole("form", { name: "Edit desk" });
    const port = within(form).getByLabelText("Port");
    await waitFor(() =>
      expect(within(port).getByText("COM8 — Silicon Labs CP210x")).toBeInTheDocument(),
    );
    expect(within(form).getByText("USB VID:PID=10C4:EA60")).toBeInTheDocument();
    expect(within(port).getByText("COM3 — Intel AMT")).toBeInTheDocument();
  });

  it("binds a Call button to selecting a camera and saves it with the controller", async () => {
    await showConfigPage();
    fireEvent.click(
      within(screen.getByTestId("controller-desk")).getByRole("button", { name: "Edit" }),
    );
    answer({ ports: [] });
    const form = screen.getByRole("form", { name: "Edit desk" });

    fireEvent.change(within(form).getByLabelText("Call 2 action"), {
      target: { value: "select_camera" },
    });
    fireEvent.change(within(form).getByLabelText("Call 2 camera"), { target: { value: "wide" } });
    fireEvent.change(within(form).getByLabelText("Call 5 action"), {
      target: { value: "recall_preset" },
    });
    fireEvent.change(within(form).getByLabelText("Preset for Call 5"), { target: { value: "3" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.values.controllers![0]!.call_bindings).toEqual([
      { n: 2, action: "select_camera", camera: "wide", preset: null },
      { n: 5, action: "recall_preset", camera: null, preset: 2 },
    ]);
  });

  it("shows the controller's recent frames and copies the raw ones", async () => {
    await showConfigPage();
    const monitor = screen.getByTestId("frames-desk");
    expect(within(monitor).getByText("move pan 1 tilt 0 zoom 0")).toBeInTheDocument();
    expect(within(monitor).getByText("FF 01 00 02 3F 00 42")).toBeInTheDocument();

    fireEvent.click(within(monitor).getByRole("button", { name: "Copy last 100 raw frames" }));
    expect(lastCommand()).toMatchObject({ command: "raw_frames", args: { controller: "desk" } });
    answer({ frames: ["FF 01 00 00 00 00 01", "FF 01 00 40 00 00 41"] });

    expect(await within(monitor).findByLabelText("Raw frames from desk")).toHaveValue(
      "FF 01 00 00 00 00 01\nFF 01 00 40 00 00 41",
    );
  });

  it("saves the motion settings", async () => {
    await showConfigPage();
    const form = screen.getByRole("form", { name: "Motion settings" });

    fireEvent.change(within(form).getByLabelText("Controller silence (ms)"), {
      target: { value: "2000" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save motion settings" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.values).toEqual({
      motion: { ...savedSettings().motion, controller_silence_ms: 2000 },
    });
  });
});
