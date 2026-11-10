/**
 * Tests for the generic module page (src/pages/ModulePage.tsx): where its
 * settings link goes.
 *
 * The page is reached from the home page's module cards (`/modules/<name>`).
 * A module with its own settings page (cameras: Camera setup) must send the
 * operator there, not to the generic Settings page, which only shows that
 * module's JSON (docs/conventions.md, "One place per setting").
 */

import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { ModulePage } from "../src/pages/ModulePage";
import { fakeSocketFactory } from "./fakeSocket";

/** Render `/modules/<name>` connected to a fake server that knows the module. */
function showModulePage(name: string, title: string) {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  const server = sockets.latest();
  server.open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter initialEntries={[`/modules/${name}`]}>
        <Routes>
          <Route path="/modules/:name" element={<ModulePage />} />
        </Routes>
      </MemoryRouter>
    </StateSocketContext.Provider>,
  );
  const home = { modules: { [name]: { title, state: "ok", message: "" } } };
  act(() => server.receive({ type: "snapshot", seq: 1, state: { home }, client_id: "desk-pc" }));
}

beforeEach(() => {
  // The recent-commands panel reads the command log over HTTP.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json([])),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("module page settings link (WP10)", () => {
  it("sends the cameras module to Camera setup", () => {
    showModulePage("cameras", "Cameras");

    expect(screen.getByRole("link", { name: "⚙ Camera setup" })).toHaveAttribute(
      "href",
      "/cameras/config",
    );
    expect(screen.queryByRole("link", { name: "Settings" })).toBeNull();
  });

  it("sends a module without its own page to the generic Settings page", () => {
    showModulePage("gateway", "Gateway");

    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/settings/gateway",
    );
  });
});
