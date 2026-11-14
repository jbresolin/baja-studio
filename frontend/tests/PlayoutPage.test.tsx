/**
 * Tests for the Playout page (src/pages/PlayoutPage.tsx and its components).
 *
 * A fake server plays the studio over the fake socket: it sends the playout
 * module's state, and the tests read the commands the page sent (and answer
 * some of them). Keys are pressed with `fireEvent` on the window, which is
 * where the page listens.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type PlayoutState, type QueueEntry } from "../src/lib/playout";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { PlayoutPage } from "../src/pages/PlayoutPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";
import { hostMessage, hostsState, message } from "./hostsFixtures";
import { clip, playoutState } from "./playoutFixtures";

interface SentCommand {
  module: string;
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

let server: FakeSocket;

/** A library with two Enduro passes, a manual intro, and a clip whose file is missing. */
function library(): PlayoutState["library"] {
  return {
    "1": clip({ id: 1, title: "Intro" }),
    "2": clip({
      id: 2,
      source: "orchestrator",
      title: "Enduro · carro 42",
      prova: "Enduro",
      car_number: 42,
    }),
    "3": clip({
      id: 3,
      source: "orchestrator",
      title: "Enduro · carro 7",
      prova: "Enduro",
      car_number: 7,
    }),
    "4": clip({
      id: 4,
      source: "orchestrator",
      title: "Suspensão · carro 9",
      prova: "Suspensão",
      car_number: 9,
      missing: true,
      queueable: false,
    }),
  };
}

function entry(entryId: number, clipId: number, title: string): QueueEntry {
  return {
    entry_id: entryId,
    clip_id: clipId,
    added_by: "web:desk-pc",
    added_at: "",
    playlist_id: null,
    playlist: null,
    title,
    duration_s: 30,
    missing: false,
  };
}

/** Render the page connected to a fake server holding `playout` as its state. */
function showPlayoutPage(playout: PlayoutState = playoutState({ library: library() })) {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <PlayoutPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() => server.receive({ type: "snapshot", seq: 1, state: { playout }, client_id: "desk-pc" }));
}

/** Every command the page sent, in order. */
function sent(): SentCommand[] {
  return server.sent as SentCommand[];
}

function lastCommand(): SentCommand {
  return sent()[sent().length - 1]!;
}

/** Answer a command the way the server does, and let the page react to the answer. */
async function reply(command: SentCommand, result: unknown) {
  await act(async () => {
    server.receive({ type: "reply", request_id: command.request_id, ok: true, result });
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("the library", () => {
  it("groups clips by prova, with manual clips last under their folder's label", () => {
    showPlayoutPage();

    // One group per prova; the Enduro group lists both of its passes.
    const enduro = screen.getByTestId("group-Enduro");
    expect(within(enduro).getByText("Enduro · carro 7")).toBeInTheDocument();
    expect(within(enduro).getByText("Enduro · carro 42")).toBeInTheDocument();
    expect(screen.getByTestId("group-Clips")).toHaveTextContent("Intro");
  });

  it("shows a clip whose file is missing but does not let it be picked", () => {
    showPlayoutPage();

    // The clip is listed, marked missing, and its checkbox is disabled.
    const missing = screen.getByTestId("clip-4");
    expect(missing).toHaveTextContent("missing");
    expect(within(missing).getByRole("checkbox")).toBeDisabled();
  });

  it("queues the picked clips in one queue_add", () => {
    showPlayoutPage();

    // Tick two clips, then press "Add to queue".
    fireEvent.click(screen.getByLabelText("Select Enduro · carro 7"));
    fireEvent.click(screen.getByLabelText("Select Intro"));
    fireEvent.click(screen.getByRole("button", { name: /Add to queue/ }));

    // One command with both ids; the server tags it with this page's source (web:desk-pc).
    expect(lastCommand()).toMatchObject({
      module: "playout",
      command: "queue_add",
      args: { clip_ids: [3, 1] },
    });
    expect(screen.getByText(/web:desk-pc/)).toBeInTheDocument();
  });
});

describe("the queue", () => {
  it("dragging an entry onto another sends queue_move with that entry's position", () => {
    showPlayoutPage(
      playoutState({
        library: library(),
        queue: [entry(10, 1, "Intro"), entry(11, 2, "Enduro 42"), entry(12, 3, "Enduro 7")],
        queue_total_s: 90,
      }),
    );

    // Drag the third entry (id 12) and drop it on the first row (index 0).
    fireEvent.dragStart(screen.getByTestId("queue-entry-12"));
    fireEvent.dragOver(screen.getByTestId("queue-entry-10"));
    fireEvent.drop(screen.getByTestId("queue-entry-10"));

    expect(lastCommand()).toMatchObject({
      command: "queue_move",
      args: { entry_id: 12, to_index: 0 },
    });
    // The estimated total time of the queue is shown.
    expect(screen.getByTestId("queue-total")).toHaveTextContent("1:30");
  });
});

describe("taking a clip", () => {
  it("Space sends take, asking the server to cue first if nothing is cued", () => {
    showPlayoutPage(playoutState({ library: library(), queue: [entry(10, 1, "Intro")] }));

    act(() => {
      fireEvent.keyDown(window, { key: " ", code: "Space" });
    });

    expect(lastCommand()).toMatchObject({ command: "take", args: { cue_if_needed: true } });
  });

  it("→ skips the clip on air; Shift+→ and Backspace drop the next clip", () => {
    showPlayoutPage(playoutState({ library: library(), queue: [entry(10, 1, "Intro")] }));

    // → is "get me out of this clip"; Shift+→ and Backspace leave the air alone.
    act(() => {
      fireEvent.keyDown(window, { key: "ArrowRight" });
      fireEvent.keyDown(window, { key: "ArrowRight", shiftKey: true });
      fireEvent.keyDown(window, { key: "Backspace" });
    });

    expect(sent().map((command) => command.command)).toEqual(["skip", "drop_next", "drop_next"]);
  });

  it("Space typed in the search box searches instead of taking", () => {
    showPlayoutPage();

    // The keydown comes from the search field, where keys are typing.
    fireEvent.keyDown(screen.getByLabelText("Search the library"), { key: " " });

    expect(sent().filter((command) => command.command === "take")).toEqual([]);
  });

  it("asks for a second press when the server armed the take confirmation", async () => {
    showPlayoutPage(playoutState({ library: library(), queue: [entry(10, 1, "Intro")] }));

    // The first Take only arms the confirmation; the server says so in its reply.
    fireEvent.click(screen.getByRole("button", { name: "Take" }));
    await reply(lastCommand(), { status: "confirm", window_s: 3 });

    expect(screen.getByRole("alert")).toHaveTextContent("Press Take again within 3 s");
  });

  it("shows the confirmation prompt to every browser while the server holds it armed", () => {
    // Another operator pressed Take; this page learns it from the state.
    showPlayoutPage(
      playoutState({ take_confirmation: { requested_by: "web:tablet", window_s: 3 } }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent("asked by web:tablet");
  });
});

describe("the program strip", () => {
  it("colours the countdown red with four seconds left", () => {
    const onAir = clip({ id: 2, title: "Enduro · carro 42", duration_s: 30 });
    showPlayoutPage(
      playoutState({
        program: {
          clip: onAir,
          slot: "a",
          started_at: "",
          position_s: 26,
          duration_s: 30,
          remaining_s: 4,
        },
      }),
    );

    const countdown = screen.getByTestId("countdown");
    expect(countdown).toHaveClass("countdown-red");
    expect(countdown).toHaveTextContent("−0:04");
  });
});

describe("the program strip (WP5b)", () => {
  const onAir = clip({ id: 2, title: "Enduro · carro 42", duration_s: 40 });
  const program = {
    clip: onAir,
    slot: "a" as const,
    started_at: "",
    position_s: 10,
    duration_s: 40,
    remaining_s: 30,
  };

  it("fills its background with the clip's progress and shows what is left of the queue", () => {
    showPlayoutPage(
      playoutState({
        program,
        preview: { clip: clip({ id: 3, duration_s: 20 }), slot: "b" },
        queue: [entry(10, 1, "Intro"), { ...entry(11, 4, "Unknown"), duration_s: null }],
      }),
    );

    // A quarter played: the background is filled to 25 %.
    expect(screen.getByTestId("program-strip").style.getPropertyValue("--progress")).toBe("25.0%");
    expect(screen.getByTestId("countdown")).toHaveTextContent("−0:30");
    // After it: the cued 20 s and the queued 30 s, plus one clip of unknown length.
    expect(screen.getByTestId("queue-remaining")).toHaveTextContent("queue −0:50 +?");
  });

  it("warns on the strip when there is nowhere to return to", () => {
    const warning = "no return scene chosen: pick one under Return to";
    showPlayoutPage(
      playoutState({
        program,
        return_target: {
          mode: "chosen",
          chosen_scene: null,
          previous_scene: "Main",
          scene: null,
          warning,
        },
      }),
    );

    expect(screen.getByTestId("return-warning")).toHaveTextContent(warning);
  });
});

describe("the Return to control", () => {
  it("chooses between the previous scene and a scene picked from OBS's list", () => {
    // The server says "chosen" is selected, with Main picked.
    showPlayoutPage(
      playoutState({
        return_target: {
          mode: "chosen",
          chosen_scene: "Main",
          previous_scene: null,
          scene: "Main",
          warning: null,
        },
      }),
    );

    fireEvent.change(screen.getByLabelText("Chosen return scene"), {
      target: { value: "Interview" },
    });
    expect(lastCommand()).toMatchObject({
      command: "set_return_target",
      args: { mode: "chosen", scene: "Interview" },
    });

    fireEvent.click(screen.getByRole("radio", { name: /previous scene/ }));
    expect(lastCommand()).toMatchObject({
      command: "set_return_target",
      args: { mode: "previous" },
    });
  });
});

describe("tools, unmatched files and playlists", () => {
  it("shows one banner when ffprobe is missing, and ? for durations", () => {
    const problem =
      "durations and thumbnails unavailable — ffprobe not found; set the path in Settings or install ffmpeg";
    showPlayoutPage(
      playoutState({
        library: { "1": clip({ id: 1, duration_s: null }) },
        tools: { ffprobe: null, ffmpeg: null, problem },
      }),
    );

    expect(screen.getByTestId("tools-banner")).toHaveTextContent(problem);
    const duration = screen.getByTestId("duration-1");
    expect(duration).toHaveTextContent("?");
    expect(duration).toHaveAttribute("title", problem);
  });

  it("marks unmatched files and hides them with one toggle", () => {
    showPlayoutPage(
      playoutState({
        library: {
          "1": clip({ id: 1, title: "Intro" }),
          "5": clip({ id: 5, title: "1.v2", kind: "unmatched" }),
        },
      }),
    );

    const unmatched = screen.getByTestId("clip-5");
    expect(unmatched).toHaveTextContent("unmatched");
    expect(unmatched).toHaveAttribute("title", "file not in the orchestrator manifest");

    fireEvent.click(screen.getByLabelText("hide unmatched"));

    expect(screen.queryByTestId("clip-5")).toBeNull();
    expect(screen.getByTestId("clip-1")).toBeInTheDocument();
  });

  it("queues a whole playlist with one click", () => {
    showPlayoutPage(
      playoutState({
        library: library(),
        playlists: [
          {
            id: 7,
            name: "Patrocinadores",
            shuffle: true,
            on_air: null,
            clip_ids: [1, 2],
            created_at: "",
          },
        ],
      }),
    );

    const row = screen.getByTestId("playlist-7");
    expect(row).toHaveTextContent("Patrocinadores");
    expect(row).toHaveTextContent("2 clips · 1:00");
    fireEvent.click(within(row).getByRole("button", { name: "Queue" }));

    expect(lastCommand()).toMatchObject({ command: "playlist_queue", args: { playlist_id: 7 } });
  });

  it("makes a playlist of a whole folder group", () => {
    showPlayoutPage(
      playoutState({ library: { "2": clip({ id: 2, folder_label: "Patrocinadores" }) } }),
    );

    fireEvent.click(
      within(screen.getByTestId("group-Patrocinadores")).getByRole("button", {
        name: "+ playlist",
      }),
    );

    expect(lastCommand()).toMatchObject({
      command: "playlist_create",
      args: { group: "Patrocinadores" },
    });
  });

  it("says the queue is empty instead of showing 0:00, and +? for unknown lengths", () => {
    showPlayoutPage();
    expect(screen.getByLabelText("Queue")).toHaveTextContent("Queue empty");
    expect(screen.queryByTestId("queue-total")).toBeNull();
  });

  it("shows a new server notice once as a toast, but not one from before the page opened", () => {
    showPlayoutPage(playoutState({ notice: { id: 3, message: "old news" } }));
    expect(screen.queryByText("old news")).toBeNull();

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "playout",
        path: "notice",
        value: { id: 4, message: "on-air action audio.run_macro failed" },
      }),
    );

    expect(screen.getByText("on-air action audio.run_macro failed")).toBeInTheDocument();
  });
});

describe("messaging the hosts (WP9)", () => {
  it("has no message box while the hosts module is off", () => {
    showPlayoutPage();

    expect(screen.queryByRole("form", { name: "Message hosts" })).toBeNull();
  });

  it("sends a message to a seat from the strip without leaving the page", () => {
    showPlayoutPage();
    act(() =>
      server.receive({ type: "patch", seq: 2, module: "hosts", path: "", value: hostsState() }),
    );
    const box = screen.getByRole("form", { name: "Message hosts" });

    fireEvent.change(within(box).getByLabelText("To"), { target: { value: "2" } });
    fireEvent.change(within(box).getByLabelText("Message"), { target: { value: "30 s" } });
    fireEvent.click(within(box).getByRole("button", { name: "Send" }));

    expect(lastCommand()).toMatchObject({
      module: "hosts",
      command: "send_message",
      args: { to: 2, text: "30 s", kind: "info" },
    });
  });

  it("puts the session's messages to the hosts in the aired log download", async () => {
    showPlayoutPage();
    act(() =>
      server.receive({ type: "patch", seq: 2, module: "hosts", path: "", value: hostsState() }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Export aired log" }));
    await reply(lastCommand(), { stream_start_marker: null, entries: [], chapters: null });
    expect(lastCommand()).toMatchObject({ module: "hosts", command: "export_messages" });
    await reply(lastCommand(), { messages: [message({ id: 1 }), message({ id: 2 })] });

    expect(screen.getByText("0 airings, 2 messages to the hosts.")).toBeInTheDocument();
  });

  it("shows unread messages from the hosts with the latest one, and marks them read (WP9b)", () => {
    showPlayoutPage();
    const messages = [
      hostMessage({ id: 1, seat: 1, text: "preciso de 1 min" }),
      hostMessage({ id: 2, seat: 2, text: "problema no áudio" }),
      hostMessage({ id: 3, seat: 1, text: "pronto", read_at: "x" }),
    ];
    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "hosts",
        path: "",
        value: hostsState({ messages }),
      }),
    );

    // Two unread; the latest unread is Bruno's, in his colour.
    const line = screen.getByTestId("hosts-unread");
    expect(line).toHaveTextContent("2 Bruno: problema no áudio");
    fireEvent.click(within(line).getByRole("button", { name: "Read" }));

    expect(lastCommand()).toMatchObject({
      module: "hosts",
      command: "mark_read",
      args: { message_ids: [1, 2] },
    });
  });

  it("shows no unread line when every host message has been read", () => {
    showPlayoutPage();
    const read = [hostMessage({ id: 1, seat: 1, read_at: "x" })];
    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "hosts",
        path: "",
        value: hostsState({ messages: read }),
      }),
    );

    expect(screen.queryByTestId("hosts-unread")).toBeNull();
  });
});

/** Playout state with "Intro" (10 s at 25 fps) on air, 2 s in, playing or paused. */
function onAirState(transportState: "playing" | "paused" = "playing", startedAt = "t1") {
  const intro = clip({ id: 1, title: "Intro", duration_s: 10, fps: 25 });
  return playoutState({
    library: library(),
    program: {
      clip: intro,
      slot: "a",
      started_at: startedAt,
      position_s: 2,
      duration_s: 10,
      remaining_s: 8,
      speed_percent: 100,
      transport: { state: transportState, position_s: 2, fps: 25, speed_percent: 100 },
    },
  });
}

describe("media controls for the clip on air (WP5c)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("are there as soon as a clip is on air, marked LIVE, with no lock to open", () => {
    // João, 2026-11-14: the two-second hold-to-unlock went; the controls
    // are always available while a clip is on air.
    showPlayoutPage(onAirState());

    const controls = screen.getByRole("group", { name: "Media controls (live)" });
    expect(controls).toHaveTextContent("LIVE");
    expect(screen.queryByRole("button", { name: /Hold to unlock/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Lock" })).toBeNull();
  });

  it("pause, restart, nudges and speed each send one command for the clip on air", () => {
    showPlayoutPage(onAirState());
    const controls = screen.getByRole("group", { name: "Media controls (live)" });

    fireEvent.click(within(controls).getByRole("button", { name: "Pause" }));
    fireEvent.click(within(controls).getByRole("button", { name: "Restart" }));
    fireEvent.click(within(controls).getByRole("button", { name: "Back 5 s" }));
    fireEvent.click(within(controls).getByRole("button", { name: "Forward 5 s" }));
    fireEvent.click(within(controls).getByRole("radio", { name: "50 %" }));

    expect(sent().map(({ command, args }) => ({ command, args }))).toEqual([
      { command: "transport", args: { action: "pause" } },
      { command: "transport", args: { action: "restart" } },
      { command: "transport", args: { action: "nudge", seconds: -5 } },
      { command: "transport", args: { action: "nudge", seconds: 5 } },
      { command: "set_program_speed", args: { speed_percent: 50 } },
    ]);
  });

  it("a double click on a speed sends one change, and the buttons wait for its answer", async () => {
    showPlayoutPage(onAirState());
    const speeds = screen.getByRole("radiogroup", { name: "Speed on air" });

    fireEvent.click(within(speeds).getByRole("radio", { name: "50 %" }));
    fireEvent.click(within(speeds).getByRole("radio", { name: "50 %" }));

    // One command; until it answers, every speed is disabled and says so.
    expect(sent().filter((command) => command.command === "set_program_speed")).toHaveLength(1);
    expect(within(speeds).getByRole("radio", { name: "25 %" })).toBeDisabled();
    expect(within(speeds).getByRole("status")).toHaveTextContent("changing");

    await reply(lastCommand(), { status: "done", speed_percent: 50 });

    expect(within(speeds).getByRole("radio", { name: "25 %" })).toBeEnabled();
  });

  it("shows 'changing…' while the server runs a change, from any operator", () => {
    showPlayoutPage(onAirState());

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "playout",
        path: "speed_change",
        value: { changing: true, target: 25, error: null },
      }),
    );

    const speeds = screen.getByRole("radiogroup", { name: "Speed on air" });
    expect(within(speeds).getByRole("status")).toHaveTextContent("changing to 25 %…");
    expect(within(speeds).getByRole("radio", { name: "100 %" })).toBeDisabled();
  });

  it("says why the last speed change failed", () => {
    showPlayoutPage(onAirState());

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "playout",
        path: "speed_change",
        value: { changing: false, target: null, error: "too close to the end of the clip" },
      }),
    );

    const speeds = screen.getByRole("radiogroup", { name: "Speed on air" });
    expect(within(speeds).getByRole("alert")).toHaveTextContent(
      "Speed not changed: too close to the end of the clip",
    );
  });

  it("offers no frame step, and Play once the clip on air is paused", () => {
    // OBS can only jump to keyframes (LIVESTACK-56): a frame step cannot work.
    showPlayoutPage(onAirState("playing"));
    expect(screen.queryByRole("button", { name: "One frame forward" })).toBeNull();
    expect(screen.queryByRole("button", { name: "One frame back" })).toBeNull();

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "playout",
        path: "program.transport.state",
        value: "paused",
      }),
    );

    // Paused, the strip says so to every operator, and the button offers Play.
    expect(screen.getByTestId("program-strip")).toHaveTextContent("paused");
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });

  it("says which speed buttons replay and which continue", () => {
    showPlayoutPage(onAirState());
    const speeds = screen.getByRole("radiogroup", { name: "Speed on air" });

    // At 100 %, the slower speeds are replays (they jump back).
    expect(within(speeds).getByRole("radio", { name: "50 %" })).toHaveAttribute(
      "title",
      "Replay: jump back and play at 50 %",
    );
    expect(within(speeds).getByRole("radio", { name: "100 %" })).toHaveAttribute(
      "title",
      "The speed on air",
    );
  });

  it("the scrub bar previews where it will go while dragged and seeks once, on release", () => {
    showPlayoutPage(onAirState());
    const scrub = screen.getByRole("slider", { name: "Scrub" });

    fireEvent.change(scrub, { target: { value: "4.2" } });
    fireEvent.change(scrub, { target: { value: "6.4" } });

    // Nothing sent while dragging; the target is shown beside the bar.
    expect(sent()).toEqual([]);
    expect(screen.getByTestId("scrub-position")).toHaveTextContent("→ 0:06.4");

    fireEvent.pointerUp(scrub);

    expect(sent().map((command) => command.args)).toEqual([{ action: "seek", position_s: 6.4 }]);
  });

  it("K and J act on air; L stays Replay last; comma and period do nothing", () => {
    // With the lock gone, L could not be both "+5 s" and "Replay last" (the
    // 2026 key): it stays Replay last, and +5 s is the button only.
    showPlayoutPage(onAirState("paused"));

    act(() => {
      for (const key of ["k", "j", "l", ",", "."]) fireEvent.keyDown(window, { key });
    });

    expect(sent().map(({ command, args }) => ({ command, args }))).toEqual([
      { command: "transport", args: { action: "toggle" } },
      { command: "transport", args: { action: "nudge", seconds: -5 } },
      { command: "replay_last", args: {} },
    ]);
  });

  it("stay available when the next clip goes to air", () => {
    showPlayoutPage(onAirState("playing", "t1"));

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "playout",
        path: "program",
        value: onAirState("playing", "t2").program,
      }),
    );

    expect(screen.getByRole("group", { name: "Media controls (live)" })).toBeInTheDocument();
  });

  it("are not shown with nothing on air, and K does nothing then", () => {
    showPlayoutPage();
    expect(screen.queryByRole("group", { name: /Media controls/ })).toBeNull();

    act(() => {
      fireEvent.keyDown(window, { key: "k" });
    });
    expect(sent()).toEqual([]);
  });
});

describe("slow motion and the settings gear (WP5c)", () => {
  it("chooses the replay speed of the next cue, and shows a slow clip on the strip", () => {
    showPlayoutPage();

    fireEvent.click(
      within(screen.getByRole("radiogroup", { name: "Replay speed" })).getByRole("radio", {
        name: "50 %",
      }),
    );
    expect(lastCommand()).toMatchObject({
      command: "set_cue_speed",
      args: { speed_percent: 50 },
    });

    act(() =>
      server.receive({
        type: "patch",
        seq: 2,
        module: "playout",
        path: "",
        value: { ...onAirState(), program: { ...onAirState().program!, speed_percent: 50 } },
      }),
    );
    expect(screen.getByTestId("program-speed")).toHaveTextContent("50 %");
  });

  it("has a gear that opens Playout settings", () => {
    showPlayoutPage();
    expect(screen.getByRole("link", { name: "Playout settings" })).toHaveAttribute(
      "href",
      "/playout/settings",
    );
  });
});

describe("editing a playlist (WP5c)", () => {
  function showWithPlaylist() {
    showPlayoutPage(
      playoutState({
        library: library(),
        playlists: [
          {
            id: 7,
            name: "Patrocinadores",
            shuffle: false,
            on_air: null,
            clip_ids: [1, 2, 3],
            created_at: "",
          },
        ],
      }),
    );
    fireEvent.click(within(screen.getByTestId("playlist-7")).getByRole("button", { name: "Edit" }));
  }

  it("removes a clip and reorders without recreating the playlist", () => {
    showWithPlaylist();

    fireEvent.click(
      screen.getByRole("button", { name: "Remove Enduro · carro 42 from the playlist" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Move Enduro · carro 7 up in the playlist" }),
    );

    // Each change is one playlist_update with the whole new order (the
    // second is computed from the playlist as the server last sent it).
    expect(sent().map(({ command, args }) => ({ command, args }))).toEqual([
      { command: "playlist_update", args: { playlist_id: 7, clip_ids: [1, 3] } },
      { command: "playlist_update", args: { playlist_id: 7, clip_ids: [1, 3, 2] } },
    ]);
  });

  it("drags a clip to a new place in the playlist", () => {
    showWithPlaylist();

    fireEvent.dragStart(screen.getByTestId("playlist-clip-2"));
    fireEvent.drop(screen.getByTestId("playlist-clip-0"));

    expect(lastCommand()).toMatchObject({
      command: "playlist_update",
      args: { playlist_id: 7, clip_ids: [3, 1, 2] },
    });
  });

  it("adds a clip found in the library at the end", () => {
    showWithPlaylist();

    fireEvent.change(screen.getByLabelText("Add a clip to the playlist"), {
      target: { value: "intro" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Intro to the playlist" }));

    expect(lastCommand()).toMatchObject({
      command: "playlist_update",
      args: { playlist_id: 7, clip_ids: [1, 2, 3, 1] },
    });
  });

  it("finds no clip whose file is missing", () => {
    showWithPlaylist();

    fireEvent.change(screen.getByLabelText("Add a clip to the playlist"), {
      target: { value: "Suspensão" },
    });

    expect(screen.queryByRole("button", { name: /^Add Suspensão/ })).toBeNull();
  });
});
