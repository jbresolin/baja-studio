/**
 * Tests for the camera preview (src/components/cameras/CameraPreview.tsx and
 * src/lib/previews.ts).
 *
 * The preview asks the server for a still about once a second. Here `fetch`
 * is a fake that counts requests and answers with a tiny "image" and the
 * headers the server sends; the browser's object URLs are faked too (jsdom
 * has none). The cameras state comes over the fake socket, as on the real
 * page. Vitest's fake timers stand in for the one-second wait.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CameraPreview, zoomLabel } from "../src/components/cameras/CameraPreview";
import type { CamerasState, CameraView } from "../src/lib/cameras";
import {
  addToTrail,
  parseWindowHeader,
  TRAIL_LENGTH,
  type PreviewFrame,
} from "../src/lib/previews";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

/** A camera's state; by default a PTZ with an OBS source. */
function view(overrides: Partial<CameraView> = {}): CameraView {
  return {
    label: "Finish",
    owner: "studio",
    driver: "visca_ip",
    controller_address: null,
    obs_source: "Finish cam",
    presets: [],
    status: "ok",
    status_reason: "",
    route: "direct",
    mode: "manual",
    tracker: { state: "idle", target_id: null, confidence: null },
    position: null,
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

/** An OBS crop camera whose window is the middle half of a 4K picture. */
function cropView(): CameraView {
  return view({
    driver: "obs_crop",
    obs_source: "Cam 4K",
    capabilities: {
      kind: "crop",
      has_position_feedback: true,
      has_zoom: true,
      supports_auto: false,
      preset_max: 127,
    },
    crop: {
      x: 0.25,
      y: 0.25,
      w: 0.5,
      h: 0.5,
      centre_x: 0.5,
      centre_y: 0.5,
      zoom: 1,
      magnification: 2,
      source_width: 3840,
      source_height: 2160,
    },
  });
}

let server: FakeSocket;
let fetchCalls: string[];
/** What the fake server answers next: headers of a picture, or an error. */
let nextAnswer: () => Response;

function picture(headers: Record<string, string> = {}): Response {
  // A string body: jsdom's Blob is not the one fetch's Response understands.
  return new Response("jpeg", {
    status: 200,
    headers: { "Content-Type": "image/jpeg", "X-Preview-Shows": "full", ...headers },
  });
}

function show(cameraView: CameraView, props: { clickToCentre?: boolean } = {}) {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  server = sockets.latest();
  server.open();
  const state: CamerasState = {
    cameras: { cam: cameraView },
    controllers: {},
    order: ["cam"],
    motion: {},
  };
  act(() =>
    server.receive({ type: "snapshot", seq: 1, state: { cameras: state }, client_id: "pc" }),
  );
  return render(
    <StateSocketContext.Provider value={client}>
      <CameraPreview camera="cam" width={320} {...props} />
    </StateSocketContext.Provider>,
  );
}

/** Let `ms` of fake time pass, and the fetches it starts finish. */
async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

let urlCount = 0;
let revoked: string[];

beforeEach(() => {
  vi.useFakeTimers();
  fetchCalls = [];
  revoked = [];
  nextAnswer = () => picture();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      fetchCalls.push(path);
      return nextAnswer();
    }),
  );
  // jsdom has no object URLs: hand out numbered fake ones, and note the ones given back.
  URL.createObjectURL = vi.fn(() => `blob:preview-${++urlCount}`);
  URL.revokeObjectURL = vi.fn((url: string) => void revoked.push(url));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Camera preview", () => {
  it("asks for a still about once a second, and stops when it is unmounted", async () => {
    const page = show(view());
    await wait(0);

    // One request at once, for the camera at the asked width.
    expect(fetchCalls).toEqual(["/api/cameras/preview/cam?width=320"]);
    expect(screen.getByRole("img", { name: "Finish preview" })).toHaveAttribute(
      "src",
      "blob:preview-" + urlCount,
    );

    await wait(1000);
    expect(fetchCalls).toHaveLength(2);

    // The page goes away: no more requests, and its pictures are given back.
    page.unmount();
    await wait(5000);
    expect(fetchCalls).toHaveLength(2);
    expect(revoked).toContain("blob:preview-" + urlCount);
  });

  it("shows an agent camera's own snapshot of the whole frame, with no OBS source", async () => {
    // A camera node's crop camera (contract 1.1): the agent serves a picture of
    // its whole frame, so the studio needs no OBS source for its preview.
    nextAnswer = () => picture({ "X-Preview-Shows": "full" });
    const agentCrop = cropView();
    show(
      view({
        ...agentCrop,
        owner: "agent",
        driver: "agent",
        obs_source: null,
        capabilities: { ...agentCrop.capabilities!, snapshot: true },
        // An agent does not say its source's pixel size.
        crop: { ...agentCrop.crop!, source_width: undefined, source_height: undefined },
      }),
    );
    await wait(0);

    // It is asked for, and fills the whole box (not placed on the window) ...
    expect(fetchCalls).toEqual(["/api/cameras/preview/cam?width=320"]);
    const image = screen.getByRole("img", { name: "Finish preview" });
    expect(image.style.left).toBe("");
    // ... with the window's outline drawn on it.
    expect(screen.getByTestId("preview-window").style.left).toBe("25%");
  });

  it("asks for nothing when the camera has no OBS source", async () => {
    show(view({ obs_source: null }));
    await wait(3000);

    expect(fetchCalls).toEqual([]);
    expect(screen.getByText("No OBS source (Camera setup)")).toBeInTheDocument();
  });

  it("asks for nothing while the tab is hidden, and starts again when it is shown", async () => {
    // The browser says the tab is in the background.
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    show(view());
    await wait(3000);
    expect(fetchCalls).toEqual([]);

    visibility.mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await wait(0);
    expect(fetchCalls).toHaveLength(1);
  });

  it("asks for nothing while it is scrolled off screen", async () => {
    // A stand-in IntersectionObserver the test can drive: "you are off screen".
    let report: (visible: boolean) => void = () => undefined;
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
          report = (visible) => callback([{ isIntersecting: visible }]);
        }
        observe() {}
        disconnect() {}
      },
    );
    show(view());
    act(() => report(false));
    await wait(3000);
    const whileHidden = fetchCalls.length;

    await wait(3000);
    expect(fetchCalls.length).toBe(whileHidden); // nothing more while off screen
    act(() => report(true));
    await wait(0);
    expect(fetchCalls.length).toBe(whileHidden + 1);
  });

  it("shows the server's reason when there is no picture", async () => {
    nextAnswer = () => Response.json({ detail: "OBS offline: OBS quit" }, { status: 503 });
    show(view());
    await wait(0);

    expect(screen.getByText("OBS offline: OBS quit")).toBeInTheDocument();
  });

  it("draws a crop camera's window on the frame, with its zoom", async () => {
    nextAnswer = () =>
      picture({ "X-Preview-Shows": "window", "X-Crop-Window": "0.25,0.25,0.5,0.5" });
    show(cropView());
    await wait(0);

    // The box has the source's shape, 3840:2160.
    expect(screen.getByTestId("preview-cam").style.aspectRatio).toMatch(/^1\.777/);
    // The window's outline sits where the state says.
    const outline = screen.getByTestId("preview-window");
    expect(outline.style.left).toBe("25%");
    expect(outline.style.width).toBe("50%");
    // The picture (which is the window itself) is placed on the window.
    expect(screen.getByRole("img", { name: "Finish preview" }).style.left).toBe("25%");
    expect(screen.getByText("2.0×")).toBeInTheDocument();
  });

  it("keeps the windows it saw before, dimmed, around the current one", async () => {
    let window = "0,0,0.5,0.5";
    nextAnswer = () => picture({ "X-Preview-Shows": "window", "X-Crop-Window": window });
    show(cropView());
    await wait(0);
    window = "0.5,0.5,0.5,0.5"; // the window moved to the other corner
    await wait(1000);

    const images = screen.getByTestId("preview-cam").querySelectorAll("img");
    expect(images).toHaveLength(2);
    expect(images[0]).toHaveClass("preview-trail"); // the old corner, dimmed
    expect(images[1]).not.toHaveClass("preview-trail"); // the live window
    expect(images[1]!.style.left).toBe("50%");
  });

  it("a click moves a crop camera's window to the clicked point", async () => {
    show(cropView(), { clickToCentre: true });
    await wait(0);
    const box = screen.getByTestId("preview-cam");
    // The preview is 400 x 225 pixels on the page, at the top-left corner.
    box.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 400, height: 225, right: 400, bottom: 225 }) as DOMRect;

    fireEvent.click(box, { clientX: 100, clientY: 45 });

    const sent = server.sent.at(-1) as { command: string; args: Record<string, unknown> };
    expect(sent.command).toBe("centre_window");
    expect(sent.args).toEqual({ camera: "cam", x: 0.25, y: 0.2 });
  });

  it("a click on a camera that is not a crop sends nothing", async () => {
    show(view(), { clickToCentre: true });
    await wait(0);
    const before = server.sent.length;

    fireEvent.click(screen.getByTestId("preview-cam"), { clientX: 10, clientY: 10 });

    expect(server.sent.length).toBe(before);
  });
});

describe("Preview helpers", () => {
  it("reads the window header and refuses anything else", () => {
    expect(parseWindowHeader("0.1,0.2,0.5,0.5")).toEqual({ x: 0.1, y: 0.2, w: 0.5, h: 0.5 });
    expect(parseWindowHeader("0.1,nope")).toBeNull();
    expect(parseWindowHeader(null)).toBeNull();
  });

  it("a trail keeps one picture per place, and at most a dozen", () => {
    const frame = (seq: number, x: number): PreviewFrame => ({
      url: `blob:${seq}`,
      seq,
      window: { x, y: 0, w: 0.5, h: 0.5 },
    });
    // A new picture at the same place replaces the old one there.
    const same = addToTrail([frame(1, 0.1)], frame(2, 0.105), true);
    expect(same.kept.map((f) => f.seq)).toEqual([2]);
    expect(same.dropped.map((f) => f.seq)).toEqual([1]);

    // Many places: only the newest TRAIL_LENGTH stay.
    let frames: PreviewFrame[] = [];
    for (let i = 0; i < TRAIL_LENGTH + 3; i++)
      frames = addToTrail(frames, frame(i, i / 20), true).kept;
    expect(frames).toHaveLength(TRAIL_LENGTH);
    expect(frames.at(-1)!.seq).toBe(TRAIL_LENGTH + 2);

    // A whole-picture preview keeps only its latest picture.
    expect(addToTrail(frames, frame(99, 0.9), false).kept.map((f) => f.seq)).toEqual([99]);
  });

  it("writes the zoom the way the page shows it", () => {
    expect(zoomLabel(1.6)).toBe("1.6×");
  });
});
