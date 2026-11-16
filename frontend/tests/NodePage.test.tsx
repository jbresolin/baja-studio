/**
 * Tests for the camera node's own page (src/node/): status, token, settings.
 *
 * The page is served by a camera node on a trackside laptop (ADR 0023). Here
 * the node is pretend: `fetch` is a fake that answers like the node's API
 * (settings, commands, logs) and records what the page sent, and the live
 * state arrives over the fake socket, as on the real page.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { NodePage } from "../src/node/NodePage";
import {
  MASK,
  newCamera,
  socketUrl,
  TOKEN_KEY,
  type NodeSettingsValues,
} from "../src/node/nodeApi";
import { fakeSocketFactory } from "./fakeSocket";

/** The node's saved settings as its API shows them: secrets masked. */
function settings(): NodeSettingsValues {
  const node = newCamera([]);
  const obs = { ...newCamera([node]), mode: "obs" as const };
  obs.obs = { ...obs.obs, password: MASK, source: "S600 A" };
  node.video = { ...node.video, device: "dev-a", device_name: "S600", output_url: MASK };
  return {
    agent_id: "node-xps",
    name: "XPS",
    token: MASK,
    port: 8891,
    ffmpeg_path: "",
    cameras: [node, obs],
  };
}

interface Sent {
  method: string;
  path: string;
  body: unknown;
  token: string | null;
}

let sent: Sent[];
/** Whether the fake node wants a token, and which. */
let requiredToken: string | null;

beforeEach(() => {
  sent = [];
  requiredToken = null;
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const token = new Headers(init.headers).get("X-Agent-Token");
      const body = init.body ? JSON.parse(String(init.body)) : undefined;
      sent.push({ method: init.method ?? "GET", path, body, token });
      if (requiredToken !== null && token !== requiredToken) {
        return Response.json(
          { error: "unauthorized", detail: "missing or wrong" },
          { status: 401 },
        );
      }
      if (path === "/api/settings/node" && init.method === "PUT") {
        return Response.json({ version: 8, values: (body as { values: unknown }).values });
      }
      if (path === "/api/settings/node") return Response.json({ version: 7, values: settings() });
      if (path.startsWith("/api/logs")) {
        return Response.json([
          { ts: "12:00:00", level: "INFO", message: "camera cam1: node mode started" },
        ]);
      }
      if (path === "/api/commands") {
        return Response.json({ ok: true, result: { result: "connected", message: "OBS 31.0" } });
      }
      return Response.json({}, { status: 404 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function show() {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://node/ws", createSocket: sockets.create });
  client.connect();
  const server = sockets.latest();
  server.open();
  const onTokenChanged = vi.fn();
  render(
    <StateSocketContext.Provider value={client}>
      <NodePage onTokenChanged={onTokenChanged} />
    </StateSocketContext.Provider>,
  );
  return { server, onTokenChanged };
}

describe("Camera node page", () => {
  it("shows each camera's status: mode, health, reason and the video's figures", async () => {
    const { server } = show();
    act(() =>
      server.receive({
        type: "snapshot",
        seq: 1,
        client_id: "phone",
        state: {
          node: {
            order: ["cam1", "cam2"],
            usb: { status: "ok", message: "", devices: [] },
            system: { cpu_percent: 41.5, memory_percent: 50 },
            cameras: {
              cam1: {
                label: "Finish",
                mode: "node",
                status: "ok",
                details: {
                  pipeline: {
                    state: "running",
                    reason: "",
                    input_fps: 30,
                    output_fps: 30,
                    dropped_frames: 2,
                    bitrate_kbps: 8000,
                    encoder: "h264_nvenc",
                  },
                  output: "srt://192.168.10.10:8890?streamid=***",
                },
              },
              cam2: {
                label: "Jump",
                mode: "obs",
                status: "offline",
                status_reason: "OBS at 127.0.0.1:4455 unreachable: connection refused",
              },
            },
          },
        },
      }),
    );
    await screen.findByText("Settings");

    const first = screen.getByTestId("status-cam1");
    expect(first).toHaveTextContent("30 fps in, 30 fps out, 2 dropped, 8000 kbit/s, h264_nvenc");
    // Where the picture goes, without its secret parts (the node hid them).
    expect(first).toHaveTextContent("Sends to srt://192.168.10.10:8890?streamid=***");
    expect(screen.getByTestId("status-cam2")).toHaveTextContent(
      "OBS at 127.0.0.1:4455 unreachable",
    );
    expect(screen.getByText(/Laptop CPU: 41.5%/)).toBeInTheDocument();
    expect(await screen.findByText(/camera cam1: node mode started/)).toBeInTheDocument();
  });

  it("asks for the token when the node wants one, and sends it from then on", async () => {
    requiredToken = "the-node-token";
    const { onTokenChanged } = show();

    fireEvent.change(await screen.findByLabelText("Node token"), {
      target: { value: "the-node-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    // Kept for this tab only, the socket is opened again with it, and the
    // settings are read with it.
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe("the-node-token");
    expect(onTokenChanged).toHaveBeenCalled();
    await screen.findByText("Settings");
    expect(sent.at(-1)?.token).toBe("the-node-token");
  });

  it("saves with the version it read, sending masked secrets back unchanged", async () => {
    show();
    const name = await screen.findByLabelText("Node name");

    fireEvent.change(name, { target: { value: "XPS 15" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved.");

    const saved = sent.find((request) => request.method === "PUT");
    const body = saved?.body as { version: number; values: NodeSettingsValues };
    expect(body.version).toBe(7);
    expect(body.values.name).toBe("XPS 15");
    // "********" means "keep what the node has": the page never knew the secrets.
    expect(body.values.token).toBe(MASK);
    expect(body.values.cameras[0]?.video.output_url).toBe(MASK);
    expect(body.values.cameras[1]?.obs.password).toBe(MASK);
  });

  it("shows OBS fields for a camera in OBS mode, and tests the connection", async () => {
    show();
    const form = await screen.findByTestId("camera-form-cam2");
    expect(form).toHaveTextContent("Source in OBS");

    fireEvent.click(screen.getAllByRole("button", { name: "Test connection" })[0]!);

    expect(await screen.findByText("connected: OBS 31.0")).toBeInTheDocument();
    const command = sent.find((request) => request.path === "/api/commands");
    expect(command?.body).toMatchObject({
      module: "node",
      command: "test_obs_connection",
      args: { camera: "cam2", obs: { port: 4455, password: MASK } },
    });
  });

  it("adds a camera with the next free id, in node mode", () => {
    const camera = newCamera([newCamera([])]);

    expect(camera.id).toBe("cam2");
    expect(camera.mode).toBe("node");
  });

  it("puts the token in the socket's address, since a WebSocket has no headers", () => {
    expect(socketUrl("ws://node/ws", "a b")).toBe("ws://node/ws?token=a%20b");
    expect(socketUrl("ws://node/ws", "")).toBe("ws://node/ws");
  });
});
