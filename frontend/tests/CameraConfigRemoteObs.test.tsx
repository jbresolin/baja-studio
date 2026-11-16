/**
 * Tests for crop cameras in a remote OBS on Camera setup (WP13, ADR 0021):
 * the "OBS" choice (this PC / a laptop), Test connection, the source list
 * from the chosen OBS, the write-only password, and which OBS each crop
 * camera uses in the camera list.
 *
 * As in CameraConfigPage.test.tsx, the page reads and saves the cameras
 * settings over HTTP (`fetch`, faked by a tiny in-memory server that records
 * every save) and sends commands over the WebSocket (a fake socket the test
 * plays: we read what the page sent and answer it as the studio would).
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { defaultCropSettings, newCamera, type CameraConfig } from "../src/lib/cameras";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { CameraConfigPage } from "../src/pages/CameraConfigPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

const MASK = "********"; // how the settings API shows a saved password

/** A crop camera on this PC's OBS, and one on a laptop's (its password saved). */
function savedCameras(): CameraConfig[] {
  const local: CameraConfig = {
    ...newCamera("studio-crop"),
    driver: "obs_crop",
    obs_source: "Cam 4K",
  };
  const remote: CameraConfig = {
    ...newCamera("lap-a"),
    driver: "obs_crop",
    obs_source: "Laptop cam A",
    crop: {
      ...defaultCropSettings(),
      obs: { remote: true, host: "192.168.10.50", port: 4455, password: MASK },
    },
  };
  return [local, remote];
}

let saves: { version: number; values: { cameras?: CameraConfig[] } }[];
let server: FakeSocket;

beforeEach(() => {
  saves = [];
  let document = {
    section: "cameras",
    version: 3,
    updated_at: "",
    values: {
      enabled: true,
      cameras: savedCameras(),
      controllers: [],
      motion: {
        joystick_deadband: 0.04,
        ttl_ms: 300,
        resend_interval_ms: 100,
        joystick_zoom_velocity: 0.6,
        controller_silence_ms: 0,
      },
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path !== "/api/settings/cameras") return new Response("{}", { status: 404 });
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        saves.push(body);
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

/** Open Camera setup with the laptop camera running (and a note about its connection). */
async function showConfigPage(note = "") {
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
        cameras: {
          order: ["studio-crop", "lap-a"],
          motion: {},
          controllers: {},
          cameras: {
            "lap-a": {
              label: "lap-a",
              status: "degraded",
              status_reason: "OBS at 192.168.10.50:4455 refused the password",
              presets: [],
              capabilities: { preset_max: 127, kind: "crop" },
              control: {},
              obs: { remote: true, address: "192.168.10.50:4455", note },
            },
          },
        },
      },
    }),
  );
  await screen.findByTestId("camera-list");
}

function sentCommands(name: string): SentCommand[] {
  return (server.sent as SentCommand[]).filter((message) => message.command === name);
}

/** Answer the last command called `name` with `result`, as the studio would. */
function answer(name: string, result: unknown) {
  const command = sentCommands(name).at(-1)!;
  act(() => server.receive({ type: "reply", request_id: command.request_id, ok: true, result }));
}

describe("Camera setup: crop cameras in a remote OBS", () => {
  it("the camera list says which OBS each crop camera uses", async () => {
    await showConfigPage();

    const rows = within(screen.getByTestId("camera-list")).getAllByRole("row");
    // The studio PC's own OBS, and the laptop's address.
    expect(rows[0]!.textContent).toContain("OBS crop Cam 4K @ this PC");
    expect(rows[1]!.textContent).toContain("OBS crop Laptop cam A @ 192.168.10.50:4455");
  });

  it("choosing a remote OBS asks for its address, port and password", async () => {
    await showConfigPage();
    fireEvent.click(screen.getByRole("button", { name: "Add camera" }));
    const form = screen.getByRole("form", { name: "New camera" });
    fireEvent.change(within(form).getByLabelText("Driver"), { target: { value: "obs_crop" } });

    // "This PC" by default: no address fields, and the source list is playout's
    // OBS's, asked for exactly as before WP13 (no arguments).
    expect(within(form).queryByLabelText("OBS address")).not.toBeInTheDocument();
    expect(sentCommands("list_obs_sources").at(-1)!.args).toEqual({});

    fireEvent.change(within(form).getByLabelText("OBS"), { target: { value: "remote" } });

    expect(within(form).getByLabelText("OBS address")).toBeInTheDocument();
    expect(within(form).getByLabelText("OBS port")).toHaveValue(4455);
    // A password field: what is typed is never shown.
    expect(within(form).getByLabelText("OBS password")).toHaveAttribute("type", "password");
  });

  it("Test connection tests what the form says and lists that OBS's sources", async () => {
    await showConfigPage();
    fireEvent.click(screen.getByRole("button", { name: "Add camera" }));
    const form = screen.getByRole("form", { name: "New camera" });
    fireEvent.change(within(form).getByLabelText("Driver"), { target: { value: "obs_crop" } });
    fireEvent.change(within(form).getByLabelText("OBS"), { target: { value: "remote" } });
    fireEvent.change(within(form).getByLabelText("OBS address"), {
      target: { value: "192.168.10.51" },
    });
    fireEvent.change(within(form).getByLabelText("OBS password"), { target: { value: "secret" } });

    fireEvent.click(within(form).getByRole("button", { name: "Test connection" }));
    // The page sends the fields as they are (not saved yet), for a new camera.
    expect(sentCommands("test_obs_connection").at(-1)!.args).toEqual({
      obs: { remote: true, host: "192.168.10.51", port: 4455, password: "secret" },
      camera: "",
    });
    answer("test_obs_connection", {
      result: "connected",
      message: "connected to OBS 30.2.3 (obs-websocket 5.5.2) at 192.168.10.51:4455",
    });

    expect(await within(form).findByRole("status")).toHaveTextContent("✓ connected to OBS 30.2.3");
    // Connected: the page asks that OBS for its inputs, and offers them.
    await waitFor(() =>
      expect(sentCommands("list_obs_sources").at(-1)!.args).toMatchObject({
        obs: { remote: true, host: "192.168.10.51" },
      }),
    );
    answer("list_obs_sources", {
      connected: true,
      reason: "",
      sources: [
        { name: "Laptop cam A", kind: "dshow_input" },
        { name: "Laptop cam B", kind: "dshow_input" },
      ],
    });
    const source = await within(form).findByRole("combobox", { name: "OBS source" });
    expect(within(source).getByRole("option", { name: "Laptop cam B (dshow_input)" })).toBeTruthy();
  });

  it("a wrong password or an unreachable laptop is said in the studio's words", async () => {
    await showConfigPage();
    fireEvent.click(screen.getByRole("button", { name: "Add camera" }));
    const form = screen.getByRole("form", { name: "New camera" });
    fireEvent.change(within(form).getByLabelText("Driver"), { target: { value: "obs_crop" } });
    fireEvent.change(within(form).getByLabelText("OBS"), { target: { value: "remote" } });
    fireEvent.change(within(form).getByLabelText("OBS address"), { target: { value: "laptop1" } });

    fireEvent.click(within(form).getByRole("button", { name: "Test connection" }));
    answer("test_obs_connection", {
      result: "wrong_password",
      message: "OBS at laptop1:4455 refused the password",
    });

    expect(await within(form).findByRole("status")).toHaveTextContent(
      "✗ OBS at laptop1:4455 refused the password",
    );
    // Editing a field clears the old answer: it said nothing about the new values.
    fireEvent.change(within(form).getByLabelText("OBS password"), { target: { value: "x" } });
    expect(within(form).queryByRole("status")).not.toBeInTheDocument();
  });

  it("a saved camera's password stays masked and is sent back masked", async () => {
    await showConfigPage();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]!);
    const form = screen.getByRole("form", { name: "Edit lap-a" });

    // The mask, never the password: the page does not have it.
    expect(within(form).getByLabelText("OBS password")).toHaveValue(MASK);
    // The source list asks the laptop, naming the camera so the studio uses its
    // saved password for the mask.
    expect(sentCommands("list_obs_sources").at(-1)!.args).toEqual({
      obs: { remote: true, host: "192.168.10.50", port: 4455, password: MASK },
      camera: "lap-a",
    });

    fireEvent.click(within(form).getByRole("button", { name: "Test connection" }));
    expect(sentCommands("test_obs_connection").at(-1)!.args).toMatchObject({
      obs: { password: MASK },
      camera: "lap-a",
    });

    // Saving without touching the password sends the mask: "keep the saved one".
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.values.cameras![1]!.crop.obs).toEqual({
      remote: true,
      host: "192.168.10.50",
      port: 4455,
      password: MASK,
    });
  });

  it("shows what the studio says about the camera's connection", async () => {
    // Two cameras on one laptop were saved with different passwords.
    await showConfigPage(
      "camera 'lap-b' was saved later with another password for OBS at 192.168.10.50:4455",
    );
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]!);
    const form = screen.getByRole("form", { name: "Edit lap-a" });

    expect(
      within(form).getByText(/camera 'lap-b' was saved later with another password/),
    ).toBeInTheDocument();
  });

  it("switching back to this PC's OBS keeps the address for later", async () => {
    await showConfigPage();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]!);
    const form = screen.getByRole("form", { name: "Edit lap-a" });

    fireEvent.change(within(form).getByLabelText("OBS"), { target: { value: "local" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.values.cameras![1]!.crop.obs).toMatchObject({
      remote: false,
      host: "192.168.10.50",
    });
  });
});
