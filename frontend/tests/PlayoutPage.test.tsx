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
import { beforeEach, describe, expect, it } from "vitest";

import type { PlayoutState, QueueEntry } from "../src/lib/playout";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { PlayoutPage } from "../src/pages/PlayoutPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";
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
  it("groups clips by prova, with manual clips last", () => {
    showPlayoutPage();

    // One group per prova; the Enduro group lists both of its passes.
    const enduro = screen.getByTestId("group-Enduro");
    expect(within(enduro).getByText("Enduro · carro 7")).toBeInTheDocument();
    expect(within(enduro).getByText("Enduro · carro 42")).toBeInTheDocument();
    expect(screen.getByTestId("group-Manual")).toHaveTextContent("Intro");
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
    expect(countdown).toHaveTextContent("-0:04");
  });
});
