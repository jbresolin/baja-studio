/**
 * Tests for the playout settings page (src/pages/PlayoutSettingsPage.tsx).
 *
 * `fetch` is a tiny in-memory server: GET /api/settings/playout and audio
 * return the stored documents, PUT saves (or answers what the test set),
 * and GET /api/playout/browse lists two folders. Live state (audio's
 * macros, playout's tools) comes over the fake socket.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { PlayoutSettingsPage } from "../src/pages/PlayoutSettingsPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";
import { playoutState } from "./playoutFixtures";

function playoutSettings() {
  return {
    enabled: true,
    sources: { orchestrator: { enabled: false }, local_folders: [] },
    obs: {
      host: "localhost",
      port: 4455,
      password: "",
      scene_a: "Playback A",
      scene_b: "Playback B",
      source_a: "baja_program_a",
      source_b: "baja_program_b",
      post_clip: "return",
      fade_ms: 1000,
      cut_transition: "Cut",
      fade_transition: "Fade",
    },
    tools: { ffprobe_path: "ffprobe", ffmpeg_path: "ffmpeg" },
    on_air_actions: { on_clip_start: [], on_clip_end: [] },
  };
}

let saves: { version: number; values: ReturnType<typeof playoutSettings> }[];
let nextSaveAnswer: Response | null;

beforeEach(() => {
  saves = [];
  nextSaveAnswer = null;
  let stored = { section: "playout", version: 3, updated_at: "", values: playoutSettings() };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path.startsWith("/api/playout/browse")) {
        return Response.json({
          path: "D:\\",
          parent: "",
          folders: [
            { name: "clips", path: "D:\\clips" },
            { name: "sponsors", path: "D:\\sponsors" },
          ],
          error: null,
        });
      }
      if (path === "/api/settings/audio") {
        return Response.json({
          section: "audio",
          version: 1,
          updated_at: "",
          values: { macros: [{ id: "mute-track", label: "Mute trackside" }] },
        });
      }
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        saves.push(body);
        if (nextSaveAnswer) return nextSaveAnswer;
        stored = { ...stored, version: stored.version + 1, values: body.values };
      }
      return Response.json(stored);
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

// The fake studio on the other end of the state socket (live state, commands).
let socketServer: FakeSocket;

async function showSettings() {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  const server = sockets.latest();
  socketServer = server;
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <PlayoutSettingsPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() =>
    server.receive({
      type: "snapshot",
      seq: 1,
      client_id: "me",
      state: { playout: playoutState(), audio: { macros: { "mute-track": { active: false } } } },
    }),
  );
  await screen.findByText("Clip folders");
}

describe("the folders form", () => {
  it("saves folders as objects with a label and one receiver root", async () => {
    // Regression, bench 2026-11-08: a JSON box took ["D:/clips"]. The form
    // can only produce the right shape.
    await showSettings();

    fireEvent.click(screen.getByRole("button", { name: "Add folder" }));
    fireEvent.change(screen.getByLabelText("Folder 1 path"), { target: { value: "D:\\clips" } });
    fireEvent.click(screen.getByRole("button", { name: "Add folder" }));
    fireEvent.change(screen.getByLabelText("Folder 2 path"), {
      target: { value: "D:\\sponsors" },
    });
    fireEvent.change(screen.getByLabelText("Folder 2 label"), {
      target: { value: "Patrocinadores" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    });

    expect(saves).toHaveLength(1);
    expect(saves[0]!.version).toBe(3);
    expect(saves[0]!.values.sources.local_folders).toEqual([
      { path: "D:\\clips", label: "", recursive: true, receiver_root: true },
      { path: "D:\\sponsors", label: "Patrocinadores", recursive: true, receiver_root: false },
    ]);
  });

  it("picks a folder by browsing the streaming PC's own disks", async () => {
    await showSettings();
    fireEvent.click(screen.getByRole("button", { name: "Add folder" }));

    fireEvent.click(screen.getByRole("button", { name: "browse" }));
    const browser = await screen.findByTestId("folder-browser");
    fireEvent.click(within(browser).getByRole("button", { name: /clips/ }));
    // The fake server lists the same folders wherever we are; pick the one shown.
    fireEvent.click(await within(browser).findByRole("button", { name: "Use this folder" }));

    expect(screen.getByLabelText("Folder 1 path")).toHaveValue("D:\\");
  });

  it("lists every field the server refused, with its path", async () => {
    await showSettings();
    nextSaveAnswer = Response.json(
      {
        detail: [
          {
            loc: ["body", "sources", "local_folders", 0, "path"],
            msg: "String should have at least 1 character",
          },
        ],
      },
      { status: 422 },
    );
    fireEvent.click(screen.getByRole("button", { name: "Add folder" }));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    });

    expect(screen.getByTestId("settings-errors")).toHaveTextContent(
      "body.sources.local_folders.0.path: String should have at least 1 character",
    );
  });
});

describe("on air actions and advanced JSON", () => {
  it("adds an audio macro, listed by its label, to run when a clip starts", async () => {
    await showSettings();

    const start = screen.getByTestId("on-air-on_clip_start");
    const picker = within(start).getByLabelText(/Add an audio macro/);
    await within(picker).findByRole("option", { name: "Mute trackside" });
    fireEvent.change(picker, { target: { value: "mute-track" } });

    expect(start).toHaveTextContent("audio macro: Mute trackside");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    });
    expect(saves[0]!.values.on_air_actions.on_clip_start).toEqual([
      { module: "audio", command: "run_macro", args: { macro: "mute-track" } },
    ]);
  });

  it("says when the advanced JSON is not valid instead of silently keeping the old value", async () => {
    await showSettings();
    fireEvent.click(screen.getByText("Advanced: all settings as JSON"));

    // A Windows path with a single backslash is not valid JSON.
    fireEvent.change(screen.getByLabelText("Settings JSON"), {
      target: { value: '{"sources": {"local_folders": [{"path": "D:\\clips"}]}}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply JSON" }));

    expect(screen.getByText(/Not valid JSON, nothing applied/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("tools, speed changes and rescanning (WP5c)", () => {
  it("says blank means the bundled copy, and which copy is in use", async () => {
    await showSettings();
    act(() =>
      socketServer.receive({
        type: "patch",
        seq: 2,
        module: "playout",
        path: "tools",
        value: {
          ffprobe: "C:\\Baja Studio\\ffmpeg\\ffprobe.exe",
          ffmpeg: null,
          sources: { ffprobe: "bundled", ffmpeg: "PATH" },
          problem: "thumbnails unavailable — ffmpeg not found",
        },
      }),
    );

    expect(screen.getAllByText(/Leave blank for the bundled copy/)).toHaveLength(2);
    expect(screen.getByTestId("tool-found-ffprobe")).toHaveTextContent(
      "In use: bundled (C:\\Baja Studio\\ffmpeg\\ffprobe.exe)",
    );
    expect(screen.getByTestId("tool-found-ffmpeg")).toHaveTextContent("In use: not found");
  });

  it("saves how far a slow-motion replay jumps back", async () => {
    await showSettings();

    fireEvent.change(screen.getByLabelText("Replay jumps back (s)"), {
      target: { value: "5" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    });

    expect(saves[0]!.values.obs).toMatchObject({ replay_jump_back_s: 5 });
  });

  it("rescans the library and shows the files measured so far", async () => {
    await showSettings();
    let seq = 1;
    const library = (pending: number) =>
      act(() =>
        socketServer.receive({
          type: "patch",
          seq: ++seq,
          module: "playout",
          path: "library_summary",
          value: {
            total: 5,
            missing: 1,
            pending_probe: pending,
            unplayed: 0,
            unmatched: 0,
            per_group: {},
          },
        }),
      );

    fireEvent.click(screen.getByRole("button", { name: "Rescan library" }));
    const command = socketServer.sent[socketServer.sent.length - 1] as {
      command: string;
      request_id: string;
    };
    expect(command.command).toBe("rescan_library");
    await act(async () => {
      socketServer.receive({
        type: "reply",
        request_id: command.request_id,
        ok: true,
        result: { files: 4 },
      });
    });
    library(3);

    // Four present files (five, one missing); one measured so far.
    expect(screen.getByTestId("rescan-progress")).toHaveTextContent("1 of 4 files measured");
    expect(screen.getByRole("button", { name: "Rescan library" })).toBeDisabled();

    library(0);
    expect(screen.getByTestId("rescan-progress")).toHaveTextContent(
      "Rescan done: 4 files measured.",
    );
  });
});
