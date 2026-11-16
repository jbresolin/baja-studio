/**
 * Tests for the frame around every page (src/App.tsx): the top menu.
 *
 * The Agents page (the trackside laptops: the MV app's zone agent and the
 * camera nodes) used to be reachable only from its card on Home. The menu
 * now links to it too, next to Camera setup, where agent cameras are added.
 */

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "../src/App";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { fakeSocketFactory } from "./fakeSocket";

function renderApp() {
  // The menu's connection light needs a state socket; a fake one never connects anywhere.
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>
        <App />
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
}

describe("the top menu", () => {
  it("links to the Agents page", () => {
    renderApp();

    const link = screen.getByRole("link", { name: "Agents" });
    expect(link.getAttribute("href")).toBe("/modules/agents");
  });

  it("puts Agents right after Camera setup", () => {
    renderApp();

    const names = screen.getAllByRole("link").map((link) => link.textContent);
    expect(names.indexOf("Agents")).toBe(names.indexOf("Camera setup") + 1);
  });
});
