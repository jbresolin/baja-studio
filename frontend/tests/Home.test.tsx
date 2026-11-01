/**
 * Tests for the Home page (src/pages/Home.tsx): one card per module, with a
 * health badge that follows the live state.
 */

import { act, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { Home } from "../src/pages/Home";
import { fakeSocketFactory } from "./fakeSocket";

/** Render Home connected to a fake server; returns the fake server socket. */
function showHome() {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  sockets.latest().open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  return sockets.latest();
}

describe("Home page", () => {
  it("waits politely until the first state arrives", () => {
    showHome();

    expect(screen.getByText(/Waiting for the studio service/)).toBeInTheDocument();
  });

  it("shows a card per module with its health and links to its page", () => {
    const server = showHome();

    act(() =>
      server.receive({
        type: "snapshot",
        seq: 1,
        state: {
          home: {
            modules: {
              heartbeat: { title: "Heartbeat (demo)", state: "ok", message: "" },
              echo: { title: "Echo", state: "offline", message: "child exited (code -9)" },
            },
          },
        },
      }),
    );

    const heartbeat = screen.getByTestId("card-heartbeat");
    expect(within(heartbeat).getByText("Heartbeat (demo)")).toBeInTheDocument();
    expect(within(heartbeat).getByText("ok")).toBeInTheDocument();
    expect(heartbeat).toHaveAttribute("href", "/modules/heartbeat");
    // A module with a problem shows its last error on the card.
    expect(
      within(screen.getByTestId("card-echo")).getByText("child exited (code -9)"),
    ).toBeInTheDocument();
  });

  it("updates a badge when a health patch arrives", () => {
    const server = showHome();
    act(() =>
      server.receive({
        type: "snapshot",
        seq: 1,
        state: { home: { modules: { echo: { title: "Echo", state: "offline", message: "x" } } } },
      }),
    );

    act(() =>
      server.receive({
        type: "patch",
        module: "home",
        path: "modules.echo",
        value: { title: "Echo", state: "ok", message: "" },
        seq: 2,
      }),
    );

    expect(within(screen.getByTestId("card-echo")).getByText("ok")).toBeInTheDocument();
  });
});
