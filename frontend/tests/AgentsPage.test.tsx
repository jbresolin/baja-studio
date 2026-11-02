/**
 * Tests for the Agents page (src/pages/AgentsPage.tsx, src/components/AgentCard.tsx).
 *
 * The page reads the agents module's live state and sends it commands. Here
 * a fake server plays the studio: it sends state over the fake socket, and
 * the tests read the commands the page sent and answer them. `fetch` (used by
 * the settings form at the bottom) gets a small fake too.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentsState } from "../src/lib/agents";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { AgentsPage } from "../src/pages/AgentsPage";
import { fakeSocketFactory, type FakeSocket } from "./fakeSocket";

const AGENTS_SCHEMA = {
  properties: {
    enabled: { type: "boolean", title: "Enabled" },
    agents: { type: "array", title: "Agents", managed_elsewhere: true },
    scan_cidr: { type: "string", title: "Scan Cidr" },
  },
};

/** Live state with one agent, laptop-a, whose cam1 is in auto. */
function stateWithOneAgent(mode = "auto"): AgentsState {
  return {
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
        cameras: {
          cam1: {
            status: "ok",
            mode: mode as "auto",
            tracker: { state: "tracking", target_id: "car-12", confidence: 0.9 },
            position: { pan: 12.5, tilt: -3, zoom: 0.4 },
            moving: false,
          },
        },
        events: [],
        last_seen: "2026-11-01T12:00:00Z",
      },
    },
    scan: { running: false, cidr: "", found: [], error: "" },
  };
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path === "/api/settings/agents/schema") return Response.json(AGENTS_SCHEMA);
      if (path === "/api/settings/agents") {
        return Response.json({
          section: "agents",
          version: 3,
          updated_at: "2026-11-01T12:00:00Z",
          values: { enabled: true, agents: [], scan_cidr: "192.168.88.0/24" },
        });
      }
      return new Response("{}", { status: 404 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Render the page connected to a fake server holding `agents` as its state. */
function showAgentsPage(agents: AgentsState): FakeSocket {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  sockets.latest().open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  act(() => sockets.latest().receive({ type: "snapshot", seq: 1, state: { agents } }));
  return sockets.latest();
}

interface SentCommand {
  module: string;
  command: string;
  args: Record<string, unknown>;
  request_id: string;
}

/** The last command the page sent, answered with success so the page carries on. */
function lastCommand(server: FakeSocket): SentCommand {
  const sent = server.sent[server.sent.length - 1] as SentCommand;
  act(() => server.receive({ type: "reply", request_id: sent.request_id, ok: true, result: {} }));
  return sent;
}

describe("Agents page", () => {
  it("shows each agent's status and its cameras' live mode and tracker", () => {
    showAgentsPage(stateWithOneAgent());

    const card = screen.getByTestId("agent-laptop-a");
    expect(within(card).getAllByText("ok")[0]).toHaveClass("badge-ok");
    expect(within(card).getByText(/Trackside laptop/)).toBeInTheDocument();
    expect(within(card).getByText("Finish line")).toBeInTheDocument();
    expect(within(card).getByTestId("mode-cam1")).toHaveTextContent("auto");
    expect(within(card).getByText(/tracking car-12/)).toBeInTheDocument();
  });

  it("follows a mode change pushed by the server", () => {
    const server = showAgentsPage(stateWithOneAgent("auto"));

    // The agent reports that a joystick move paused auto.
    act(() =>
      server.receive({
        type: "patch",
        module: "agents",
        path: "agents.laptop-a.cameras",
        value: stateWithOneAgent("auto_paused").agents["laptop-a"]!.cameras,
        seq: 2,
      }),
    );

    expect(screen.getByTestId("mode-cam1")).toHaveTextContent("auto_paused");
  });

  it("scans the network typed in the box and offers to add what it found", async () => {
    const user = userEvent.setup();
    const empty = stateWithOneAgent();
    empty.agents = {};
    const server = showAgentsPage(empty);

    // The box starts with the network saved in settings.
    const box = screen.getByLabelText("Network to scan");
    await waitFor(() => expect(box).toHaveValue("192.168.88.0/24"));
    await user.clear(box);
    await user.type(box, "10.0.0.0/24");
    await user.click(screen.getByRole("button", { name: "Scan subnet" }));
    expect(lastCommand(server)).toMatchObject({ command: "scan", args: { cidr: "10.0.0.0/24" } });

    // The scan results arrive as state, so every browser sees them.
    act(() =>
      server.receive({
        type: "patch",
        module: "agents",
        path: "scan",
        seq: 2,
        value: {
          running: false,
          cidr: "10.0.0.0/24",
          error: "",
          found: [
            {
              host: "10.0.0.7",
              port: 8890,
              agent_id: "laptop-b",
              name: "Pit lane laptop",
              contract_version: "1.0",
              cameras: ["cam1", "cam2"],
              already_added: false,
            },
          ],
        },
      }),
    );
    await user.click(
      within(screen.getByTestId("scan-results")).getByRole("button", { name: "Add" }),
    );

    expect(lastCommand(server)).toMatchObject({
      command: "add",
      args: { host: "10.0.0.7", port: 8890, id: "laptop-b" },
    });
  });

  it("sets a token through a password field and never shows the old one", async () => {
    const user = userEvent.setup();
    const server = showAgentsPage(stateWithOneAgent());

    const tokenField = screen.getByLabelText("Token for laptop-a");
    expect(tokenField).toHaveAttribute("type", "password");
    expect(tokenField).toHaveValue("");
    await user.type(tokenField, "s3cret");
    await user.click(screen.getByRole("button", { name: "Set token" }));

    expect(lastCommand(server)).toMatchObject({
      command: "update",
      args: { id: "laptop-a", token: "s3cret" },
    });
  });

  it("asks once more before removing an agent", async () => {
    const user = userEvent.setup();
    const server = showAgentsPage(stateWithOneAgent());
    const sentBefore = server.sent.length;

    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(server.sent.length).toBe(sentBefore); // nothing sent yet
    await user.click(screen.getByRole("button", { name: "Really remove laptop-a" }));

    expect(lastCommand(server)).toMatchObject({ command: "remove", args: { id: "laptop-a" } });
  });

  it("leaves the agents list out of the settings form at the bottom", async () => {
    showAgentsPage(stateWithOneAgent());

    // The form draws the other agents settings, but not the list edited above.
    expect(await screen.findByLabelText("Scan Cidr")).toBeInTheDocument();
    expect(screen.queryByLabelText("Agents")).not.toBeInTheDocument();
  });
});
