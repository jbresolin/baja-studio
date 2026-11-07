/**
 * Tests for the Home page (src/pages/Home.tsx): one card per module, with a
 * health badge that follows the live state; the apps behind the gateway and
 * its TLS status; the version and the diagnostics bundle button.
 */

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

/** A snapshot with the home state and, optionally, a gateway subtree. */
function snapshot(gateway?: unknown) {
  return {
    type: "snapshot",
    seq: 1,
    state: {
      home: {
        version: "1.2.3",
        modules: { gateway: { title: "Gateway", state: "ok", message: "" } },
      },
      ...(gateway ? { gateway } : {}),
    },
  };
}

const OVERLAY = { path_prefix: "/overlay", label: "BajaOvrLay", reachable: true, error: "" };
const REVIEW = {
  path_prefix: "/review",
  label: "",
  reachable: false,
  error: "connection refused",
};
const TLS_OFF = {
  mode: "off",
  hostname: "live.example.net",
  issued: null,
  expires_at: null,
  last_error: "",
};

describe("Home page, gateway and diagnostics", () => {
  it("shows the service version", () => {
    const server = showHome();

    act(() => server.receive(snapshot()));

    expect(screen.getByTestId("version")).toHaveTextContent("Baja Studio 1.2.3");
  });

  it("shows each proxied app with its health, linked through the gateway", () => {
    const server = showHome();

    act(() =>
      server.receive(
        snapshot({
          tls: { ...TLS_OFF, mode: "dns01", issued: true, expires_at: "2027-02-04T12:00:00Z" },
          apps: [OVERLAY, REVIEW],
          public_base: "https://live.example.net",
        }),
      ),
    );

    const overlay = screen.getByTestId("app-/overlay");
    expect(overlay).toHaveAttribute("href", "https://live.example.net/overlay/");
    expect(within(overlay).getByText("ok")).toBeInTheDocument();
    // An app without a label shows its path; a silent one shows why.
    const review = screen.getByTestId("app-/review");
    expect(within(review).getByText("/review")).toBeInTheDocument();
    expect(within(review).getByText("offline")).toBeInTheDocument();
    expect(within(review).getByText("connection refused")).toBeInTheDocument();
  });

  it("links apps to port 80 of this page's host when TLS is off", () => {
    const server = showHome();

    act(() => server.receive(snapshot({ tls: TLS_OFF, apps: [OVERLAY], public_base: null })));

    // jsdom pages are served from "localhost".
    expect(screen.getByTestId("app-/overlay")).toHaveAttribute("href", "http://localhost/overlay/");
    expect(screen.getByTestId("tls-status")).toHaveTextContent("HTTPS is off");
  });

  it("explains a missing certificate", () => {
    const server = showHome();

    act(() =>
      server.receive(
        snapshot({
          tls: { ...TLS_OFF, mode: "dns01", issued: false, last_error: "rate limited" },
          apps: [],
          public_base: "https://live.example.net",
        }),
      ),
    );

    expect(screen.getByTestId("tls-status")).toHaveTextContent(
      "HTTPS on live.example.net: no valid certificate (rate limited).",
    );
  });

  it("builds a diagnostics bundle and offers it for download", async () => {
    const server = showHome();
    act(() => server.receive(snapshot()));

    await userEvent.click(screen.getByRole("button", { name: "Diagnostics bundle" }));

    // The page asked the home module for a bundle; the server answers where it is.
    const sent = server.sent.at(-1) as { request_id: string; module: string; command: string };
    expect([sent.module, sent.command]).toEqual(["home", "diagnostics_bundle"]);
    await act(async () =>
      server.receive({
        type: "reply",
        request_id: sent.request_id,
        ok: true,
        result: {
          file_name: "BajaStudio-diag-20261106-120000.zip",
          url: "/api/home/diagnostics/BajaStudio-diag-20261106-120000.zip",
        },
      }),
    );

    const link = screen.getByRole("link", { name: /Download BajaStudio-diag/ });
    expect(link).toHaveAttribute(
      "href",
      "/api/home/diagnostics/BajaStudio-diag-20261106-120000.zip",
    );
  });
});
