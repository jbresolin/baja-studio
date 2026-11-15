/**
 * Tests for the Image page (src/pages/ImagePage.tsx and src/components/imaging/).
 *
 * A fake server plays the studio over the fake socket: it sends the cameras
 * module's state (an image card per camera), and the tests read the commands
 * the page sent and answer some of them. Nothing talks to a real camera.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { CamerasState } from "../src/lib/cameras";
import type { ImageCardView, ImageControlView } from "../src/lib/imaging";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { CamerasPage } from "../src/pages/CamerasPage";
import { ImagePage } from "../src/pages/ImagePage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

interface SentCommand {
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

function control(overrides: Partial<ImageControlView> = {}): ImageControlView {
  return { value: 0, auto: null, min: 0, max: 100, step: 1, default: 0, error: "", ...overrides };
}

/** An S600's card: exposure on auto, gain (no auto mode), white balance on manual. */
function usbCard(overrides: Partial<ImageCardView> = {}): ImageCardView {
  return {
    source: "usb",
    status: "ok",
    status_reason: "",
    device: { name: "EMEET SmartCam S600", path: "usb#a", present: true, matched_by: "path" },
    agent: null,
    controls: {
      exposure: control({ value: -6, auto: true, min: -13, max: -1, default: -6 }),
      gain: control({ value: 20 }),
      white_balance: control({ value: 4600, auto: false, min: 2800, max: 6500, step: 10 }),
    },
    ...overrides,
  };
}

/** Two S600s and an agent camera, in the Cameras page's order. */
function camerasState(): CamerasState {
  const camera = (label: string) => ({ label, status: "ok", control: {} });
  return {
    order: ["s600-a", "s600-b", "trackside"],
    motion: {},
    controllers: {},
    cameras: {
      "s600-a": camera("S600 A"),
      "s600-b": camera("S600 B"),
      trackside: camera("Trackside"),
    } as unknown as CamerasState["cameras"],
    image: {
      "s600-a": usbCard(),
      "s600-b": usbCard(),
      trackside: usbCard({ source: "agent", device: null, agent: "laptop-a" }),
    },
    usb: { status: "ok", message: "", devices: [] },
  };
}

let server: FakeSocket;

function showPage(page: React.ReactNode = <ImagePage />, cameras: CamerasState = camerasState()) {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>{page}</MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() => server.receive({ type: "snapshot", seq: 1, client_id: "tab1", state: { cameras } }));
}

function lastCommand(): SentCommand {
  return server.sent.at(-1) as SentCommand;
}

/** Answer the last command: with `result`, or refuse it with `error`. */
function answer(result: unknown, error?: string) {
  const command = lastCommand();
  act(() =>
    server.receive(
      error
        ? { type: "reply", request_id: command.request_id, ok: false, error }
        : { type: "reply", request_id: command.request_id, ok: true, result },
    ),
  );
}

function card(label: string) {
  return screen.getByRole("region", { name: `Image ${label}` });
}

describe("Image page", () => {
  it("shows one card per camera in the Cameras order, each with a preview slot", () => {
    showPage();

    const cards = within(screen.getByTestId("image-grid")).getAllByRole("region");
    expect(cards.map((element) => element.getAttribute("aria-label"))).toEqual([
      "Image S600 A",
      "Image S600 B",
      "Image Trackside",
    ]);
    // WP6b's preview component is put into these marked slots.
    const slot = card("S600 B").querySelector(".preview-slot");
    expect(slot).toHaveAttribute("data-camera", "s600-b");
    expect(within(card("Trackside")).getByText("agent laptop-a")).toBeInTheDocument();
    expect(within(card("S600 A")).getByText("USB: EMEET SmartCam S600")).toBeInTheDocument();
  });

  it("moving a slider sends the new value at once", () => {
    showPage();

    fireEvent.change(within(card("S600 A")).getByLabelText("Gain"), { target: { value: "35" } });

    expect(lastCommand()).toMatchObject({
      command: "set_image",
      args: { camera: "s600-a", controls: { gain: { value: 35 } } },
    });
    // The slider shows the dragged value before the server's state catches up.
    expect(within(card("S600 A")).getByText("35")).toBeInTheDocument();
  });

  it("a control on auto has its slider greyed out; Auto toggles both ways", () => {
    showPage();
    const theCard = card("S600 A");

    expect(within(theCard).getByLabelText("Exposure")).toBeDisabled();
    fireEvent.click(within(theCard).getByLabelText("Exposure auto"));
    // Off auto keeps the value the camera chose, so nothing jumps.
    expect(lastCommand().args).toEqual({
      camera: "s600-a",
      controls: { exposure: { auto: false, value: -6 } },
    });

    fireEvent.click(within(theCard).getByLabelText("White balance auto"));
    expect(lastCommand().args).toEqual({
      camera: "s600-a",
      controls: { white_balance: { auto: true } },
    });
    // Gain has no automatic mode: no box at all.
    expect(within(theCard).queryByLabelText("Gain auto")).not.toBeInTheDocument();
  });

  it("Lock asks the server to freeze the auto values and says what it locked", async () => {
    showPage();

    fireEvent.click(within(card("S600 A")).getByRole("button", { name: "Lock" }));
    expect(lastCommand()).toMatchObject({ command: "lock_image", args: { camera: "s600-a" } });
    answer({ locked: ["exposure", "focus"] });

    expect(await within(card("S600 A")).findByText("Locked: exposure, focus")).toBeInTheDocument();
  });

  it("All auto puts every control that has an automatic mode back on it", () => {
    showPage();

    fireEvent.click(within(card("S600 A")).getByRole("button", { name: "All auto" }));

    expect(lastCommand().args).toEqual({
      camera: "s600-a",
      controls: { exposure: { auto: true }, white_balance: { auto: true } },
    });
  });

  it("Reset to the device defaults is offered for USB cameras only", () => {
    showPage();

    fireEvent.click(
      within(card("S600 B")).getByRole("button", { name: "Reset to device defaults" }),
    );
    expect(lastCommand()).toMatchObject({ command: "reset_image", args: { camera: "s600-b" } });
    expect(
      within(card("Trackside")).queryByRole("button", { name: "Reset to device defaults" }),
    ).not.toBeInTheDocument();
  });

  it("copies the settings of another camera and says which controls it lacks", async () => {
    showPage();
    const theCard = card("S600 B");

    fireEvent.change(within(theCard).getByLabelText("Copy settings to S600 B from"), {
      target: { value: "s600-a" },
    });
    fireEvent.click(within(theCard).getByRole("button", { name: "Copy" }));
    expect(lastCommand()).toMatchObject({
      command: "copy_image",
      args: { from: "s600-a", to: "s600-b" },
    });
    answer({ copied: ["gain"], skipped: ["focus"] });

    expect(
      await within(theCard).findByText("Copied; this camera has no focus"),
    ).toBeInTheDocument();
  });

  it("shows a refused value on its control and a refused command on its card", async () => {
    const cameras = camerasState();
    cameras.image!["s600-a"]!.controls.gain!.error = "500 is outside 0..100";
    showPage(<ImagePage />, cameras);

    expect(within(card("S600 A")).getByRole("alert")).toHaveTextContent("500 is outside 0..100");

    fireEvent.click(within(card("S600 B")).getByRole("button", { name: "Lock" }));
    answer(null, "EMEET SmartCam S600 is not plugged in at its USB port");
    await waitFor(() =>
      expect(within(card("S600 B")).getByRole("status")).toHaveTextContent("not plugged in"),
    );
  });

  it("an offline card keeps its last values greyed out and says why", () => {
    const cameras = camerasState();
    cameras.image!["s600-b"] = usbCard({ status: "offline", status_reason: "unplugged" });
    showPage(<ImagePage />, cameras);
    const theCard = card("S600 B");

    expect(within(theCard).getByText("unplugged")).toBeInTheDocument();
    expect(within(theCard).getByLabelText("Gain")).toBeDisabled();
    expect(within(theCard).getByRole("button", { name: "Lock" })).toBeDisabled();
  });

  it("says how to get image settings when no camera has them", () => {
    showPage(<ImagePage />, { ...camerasState(), image: {} });

    expect(screen.getByText(/No camera has image settings yet/)).toBeInTheDocument();
  });

  it("stacks the cards one per row on a phone", () => {
    // The test browser does not lay pages out, so check the stylesheet's
    // phone rule for the grid the cards sit in.
    showPage();
    expect(screen.getByTestId("image-grid")).toHaveClass("image-grid");

    const css = readFileSync(resolve(__dirname, "../src/styles.css"), "utf8");
    expect(css).toMatch(
      /@media \(max-width: 600px\)\s*\{\s*\.image-grid\s*\{\s*grid-template-columns:\s*1fr;/,
    );
  });

  it("the Cameras page links to the selected camera's image settings", () => {
    const cameras = camerasState();
    showPage(<CamerasPage />, {
      ...cameras,
      cameras: {
        "s600-a": {
          ...cameras.cameras["s600-a"]!,
          capabilities: {
            kind: "fixed",
            has_position_feedback: false,
            has_zoom: false,
            supports_auto: false,
            preset_max: null,
          },
          presets: [],
          tracker: { state: "idle", target_id: null, confidence: null },
        },
      },
      order: ["s600-a"],
    });

    expect(screen.getByRole("link", { name: "Image…" })).toHaveAttribute(
      "href",
      "/cameras/image#image-s600-a",
    );
  });
});
