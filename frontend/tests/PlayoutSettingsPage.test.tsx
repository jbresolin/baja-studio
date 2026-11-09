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
import { fakeSocketFactory } from "./fakeSocket";
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

async function showSettings() {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  const server = sockets.latest();
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
