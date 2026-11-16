/**
 * Tests for the Feeds page (src/pages/FeedsPage.tsx and src/components/feeds/).
 *
 * A fake server plays the studio: it sends the feeds module's state over the
 * fake socket, and the tests read the commands the page sent and answer
 * them. `fetch` is a small fake of the settings API, which records every
 * save so the tests can check what the page asked the server to store.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SECRET_MASK, type FeedCardState, type FeedsState } from "../src/lib/feeds";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { FeedsPage } from "../src/pages/FeedsPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

const SCHEMA = {
  properties: {
    enabled: { type: "boolean", title: "Enabled" },
    feeds: { type: "array", title: "Feeds", managed_elsewhere: true },
    rtmp_port: { type: "integer", title: "Rtmp Port" },
  },
};

/** The drone, live over RTMP, as the server's state shows it. */
function droneCard(overrides: Partial<FeedCardState> = {}): FeedCardState {
  return {
    id: "feed-drone",
    name: "drone",
    label: "Drone",
    obs_source_name: "Drone",
    kind: "drone",
    record: false,
    has_password: false,
    publish_urls: [
      { protocol: "RTMP", address: "192.168.10.20", url: "rtmp://192.168.10.20:1935/drone" },
      {
        protocol: "SRT",
        address: "192.168.10.20",
        url: "srt://192.168.10.20:8890?streamid=publish:drone&pkt_size=1316&latency=200000",
      },
    ],
    obs_url: "rtsp://127.0.0.1:8554/drone",
    status: "live",
    since: "2026-11-21T08:00:00-03:00",
    protocol: "RTMP",
    video_codec: "H264",
    width: 1280,
    height: 720,
    audio_codec: "MPEG-4 Audio",
    bitrate_kbps: 4200,
    readers: 1,
    ...overrides,
  };
}

function feedsState(overrides: Partial<FeedsState> = {}): FeedsState {
  return {
    service: {
      name: "BajaStudioFeeds",
      status: "SERVICE_RUNNING",
      answering: true,
      version: "v1.21.3",
      api: "127.0.0.1:9997",
      message: "",
      port_conflicts: [],
      log_tail: [],
    },
    config: { path: "", written_at: null, waiting_for_reload: false, error: "" },
    feeds: [droneCard()],
    other_paths: [],
    refused: [],
    addresses: ["192.168.10.20"],
    recordings: {
      folder: "C:\\ProgramData\\BajaStudio\\feeds-recordings",
      free_bytes: null,
      any_feed_records: false,
    },
    ...overrides,
  };
}

/** What the settings API holds; `saves` collects every PUT body. */
let savedFeeds: Record<string, unknown>[];
let saves: { version: number; values: Record<string, unknown> }[];

beforeEach(() => {
  savedFeeds = [
    { id: "feed-drone", name: "drone", label: "Drone", kind: "drone", record: false, password: "" },
  ];
  saves = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/api/settings/feeds/schema") return Response.json(SCHEMA);
      if (path === "/api/settings/feeds" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as (typeof saves)[number];
        saves.push(body);
        if (body.values.feeds) savedFeeds = body.values.feeds as Record<string, unknown>[];
        return Response.json(document(4));
      }
      if (path === "/api/settings/feeds") return Response.json(document(3));
      return new Response("{}", { status: 404 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function document(version: number) {
  return {
    section: "feeds",
    version,
    updated_at: "2026-11-21T08:00:00Z",
    values: { enabled: true, feeds: savedFeeds, rtmp_port: 1935 },
  };
}

/** Render the page connected to a fake server holding `feeds` as the module's state. */
function showFeedsPage(feeds: FeedsState): FakeSocket {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  sockets.latest().open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <FeedsPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() => sockets.latest().receive({ type: "snapshot", seq: 1, state: { feeds } }));
  return sockets.latest();
}

interface SentCommand {
  module: string;
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

/** The last command the page sent, answered with `result` (or refused with `error`). */
function answerLast(server: FakeSocket, result: unknown, error?: string): SentCommand {
  const sent = server.sent[server.sent.length - 1] as SentCommand;
  act(() =>
    server.receive(
      error
        ? { type: "reply", request_id: sent.request_id, ok: false, error }
        : { type: "reply", request_id: sent.request_id, ok: true, result },
    ),
  );
  return sent;
}

describe("Feeds page", () => {
  it("shows a live feed with its protocol, picture, bitrate and readers", () => {
    showFeedsPage(feedsState());

    const card = screen.getByTestId("feed-drone");
    expect(within(card).getByText("live")).toHaveClass("badge-ok");
    const details = screen.getByTestId("feed-drone-details");
    expect(details).toHaveTextContent("RTMP");
    expect(details).toHaveTextContent("1280×720 H264 + MPEG-4 Audio");
    expect(details).toHaveTextContent("4.2 Mbit/s");
    expect(details).toHaveTextContent("1 reading");
  });

  it("shows a waiting feed as waiting, in amber", () => {
    showFeedsPage(feedsState({ feeds: [droneCard({ status: "waiting", protocol: null })] }));
    const card = screen.getByTestId("feed-drone");
    expect(within(card).getByText("waiting")).toHaveClass("badge-degraded");
    expect(screen.getByTestId("feed-drone-details")).toHaveTextContent("waiting for the sender");
  });

  it("copies the sender's URL with the Copy button", async () => {
    // The clipboard exists on HTTPS pages (studio.bajasaebrasil.net) and localhost.
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    showFeedsPage(feedsState());

    const field = screen.getByLabelText("RTMP URL for drone via 192.168.10.20");
    expect(field).toHaveValue("rtmp://192.168.10.20:1935/drone");
    const copyButtons = within(screen.getByTestId("feed-drone")).getAllByRole("button", {
      name: "Copy",
    });
    await user.click(copyButtons[0]!);

    expect(writeText).toHaveBeenCalledWith("rtmp://192.168.10.20:1935/drone");
    expect(within(screen.getByTestId("feed-drone")).getByText("Copied")).toBeInTheDocument();
  });

  it("selects the URL when the page may not use the clipboard (plain http on the LAN)", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    showFeedsPage(feedsState());

    const copyButtons = within(screen.getByTestId("feed-drone")).getAllByRole("button", {
      name: "Copy",
    });
    await user.click(copyButtons[0]!);

    expect(screen.getByText(/selected: copy it with Ctrl\+C/)).toBeInTheDocument();
  });

  it("explains the password placeholder on a password-protected feed", () => {
    showFeedsPage(feedsState({ feeds: [droneCard({ has_password: true })] }));
    expect(screen.getByText(/Replace <password> with this feed's password/)).toBeInTheDocument();
  });

  it("adds a feed to OBS only after a scene is picked and confirmed", async () => {
    const user = userEvent.setup();
    const server = showFeedsPage(feedsState());
    const card = screen.getByTestId("feed-drone");

    // First click: the page only asks for OBS's scenes; nothing is created yet.
    await user.click(within(card).getByRole("button", { name: "Add to OBS" }));
    expect(answerLast(server, { scenes: ["Main", "Remote"] })).toMatchObject({
      command: "obs_scenes",
    });

    // The operator picks "Remote" and confirms: now the studio is asked to add it.
    await user.selectOptions(await within(card).findByLabelText("OBS scene for drone"), "Remote");
    await user.click(within(card).getByRole("button", { name: 'Add "Drone"' }));
    expect(answerLast(server, { source: "Drone" })).toMatchObject({
      command: "add_to_obs",
      args: { feed: "drone", scene: "Remote" },
    });
    expect(
      await within(card).findByText(/Added "Drone" to the scene "Remote"/),
    ).toBeInTheDocument();
  });

  it("shows why OBS refused, e.g. a source with that name already exists", async () => {
    const user = userEvent.setup();
    const server = showFeedsPage(feedsState());
    const card = screen.getByTestId("feed-drone");

    await user.click(within(card).getByRole("button", { name: "Add to OBS" }));
    answerLast(server, { scenes: ["Main"] });
    await user.click(await within(card).findByRole("button", { name: 'Add "Drone"' }));
    answerLast(server, null, "OBS already has a source named 'Drone'");

    expect(
      await within(card).findByText(/OBS already has a source named 'Drone'/),
    ).toBeInTheDocument();
  });

  it("adds a feed by saving the feed list in the settings", async () => {
    const user = userEvent.setup();
    showFeedsPage(feedsState());
    // The settings are loaded once the form at the bottom shows the ports.
    await screen.findByText("Rtmp Port");

    // The form at the bottom of the page (the cards' forms are closed).
    const name = screen.getAllByLabelText("Feed name").at(-1)!;
    await user.type(name, "laptop1");
    await user.selectOptions(screen.getAllByLabelText("Feed kind").at(-1)!, "laptop");
    await user.type(screen.getAllByLabelText("Feed password").at(-1)!, "pw-1234");
    await user.click(screen.getByRole("button", { name: "Add feed" }));

    // One save, of the feed list only, from the version the page loaded.
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.version).toBe(3);
    expect(Object.keys(saves[0]!.values)).toEqual(["feeds"]);
    const feeds = saves[0]!.values.feeds as Record<string, unknown>[];
    expect(feeds.map((feed) => feed.name)).toEqual(["drone", "laptop1"]);
    expect(feeds[1]).toMatchObject({ kind: "laptop", password: "pw-1234" });
  });

  it("refuses a name a sender could not use, before asking the server", async () => {
    const user = userEvent.setup();
    showFeedsPage(feedsState());
    await screen.findByText("Rtmp Port");

    await user.type(screen.getAllByLabelText("Feed name").at(-1)!, "Drone 2");

    expect(screen.getByRole("button", { name: "Add feed" })).toBeDisabled();
    expect(screen.getByText(/lowercase letters, digits/)).toBeInTheDocument();
  });

  it("keeps a saved password unchanged when the feed is edited without typing one", async () => {
    // The server only says a password exists (the mask); sending the mask back keeps it.
    savedFeeds = [{ ...savedFeeds[0]!, password: SECRET_MASK }];
    const user = userEvent.setup();
    showFeedsPage(feedsState({ feeds: [droneCard({ has_password: true })] }));
    const card = screen.getByTestId("feed-drone");
    await waitFor(() => expect(within(card).getByRole("button", { name: "Edit" })).toBeEnabled());

    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const label = within(card).getByLabelText("Feed label");
    await user.clear(label);
    await user.type(label, "Drone (DJI)");
    await user.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    const feeds = saves[0]!.values.feeds as Record<string, unknown>[];
    expect(feeds[0]).toMatchObject({ label: "Drone (DJI)", password: SECRET_MASK });
  });

  it("removes a feed only after a confirmation", async () => {
    const user = userEvent.setup();
    showFeedsPage(feedsState());
    const card = screen.getByTestId("feed-drone");
    await waitFor(() => expect(within(card).getByRole("button", { name: "Remove" })).toBeEnabled());

    await user.click(within(card).getByRole("button", { name: "Remove" }));
    expect(saves).toHaveLength(0);
    await user.click(within(card).getByRole("button", { name: "Yes, remove" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.values.feeds).toEqual([]);
  });

  it("shows why MediaMTX is down: the program on its port and its last log lines", () => {
    showFeedsPage(
      feedsState({
        service: {
          ...feedsState().service,
          answering: false,
          version: null,
          message: "TCP 1935 (RTMP) is in use by nginx.exe (pid 99)",
          port_conflicts: [
            {
              protocol: "TCP",
              port: 1935,
              purpose: "RTMP",
              holder: "nginx.exe (pid 99)",
              message: "TCP 1935 (RTMP) is in use by nginx.exe (pid 99): close it",
            },
          ],
          log_tail: ["2026/11/21 08:00:00 ERR listen tcp :1935: bind: address already in use"],
        },
        feeds: [droneCard({ status: "unknown" })],
      }),
    );

    expect(screen.getByTestId("feeds-service")).toHaveTextContent(
      "MediaMTX not answering: TCP 1935 (RTMP) is in use by nginx.exe",
    );
    expect(screen.getByTestId("port-conflicts")).toHaveTextContent("close it");
    expect(screen.getByText(/address already in use/)).toBeInTheDocument();
    expect(screen.getByTestId("feed-drone-details")).toHaveTextContent("MediaMTX is not answering");
  });

  it("lists senders refused for an unknown name", () => {
    showFeedsPage(feedsState({ refused: [{ name: "dorne", at: "2026/11/21 08:01:02" }] }));
    expect(screen.getByTestId("refused-senders")).toHaveTextContent("dorne at 08:01:02");
  });

  it("offers the ports through the generic settings form, without the feed list", async () => {
    showFeedsPage(feedsState());
    expect(await screen.findByText("Rtmp Port")).toBeInTheDocument();
    expect(screen.queryByText("Feeds", { selector: "label" })).not.toBeInTheDocument();
  });
});
