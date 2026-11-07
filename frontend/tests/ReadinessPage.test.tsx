/**
 * Tests for the Readiness page (src/pages/ReadinessPage.tsx) and the toast a
 * scheduled run shows on every page (src/components/ReadinessToast.tsx).
 *
 * The page is connected to a fake server socket; the tests play the server,
 * sending readiness state and answering the page's `run` command.
 */

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ReadinessToast } from "../src/components/ReadinessToast";
import type { ReadinessState } from "../src/lib/readiness";
import { StateSocketContext } from "../src/lib/stateHooks";
import { StateSocket } from "../src/lib/stateSocket";
import { ReadinessPage } from "../src/pages/ReadinessPage";
import { fakeSocketFactory } from "./fakeSocket";
import { check, lastRun, readinessState, READY_RUN } from "./readinessFixtures";

/** Render `page` connected to a fake server; returns the server's socket. */
function show(page: React.ReactNode) {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
  client.connect();
  sockets.latest().open();
  render(
    <StateSocketContext.Provider value={client}>
      <MemoryRouter>{page}</MemoryRouter>
    </StateSocketContext.Provider>,
  );
  return sockets.latest();
}

function snapshot(readiness: ReadinessState, seq = 1) {
  return { type: "snapshot", seq, state: { readiness } };
}

describe("Readiness page", () => {
  it("offers to run the checks before any run", () => {
    const server = show(<ReadinessPage />);
    act(() => server.receive(snapshot(readinessState())));

    expect(screen.getByText("No checks run yet since the studio started.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run all checks" })).toBeEnabled();
  });

  it("sends the run command and shows progress per module while it runs", async () => {
    const server = show(<ReadinessPage />);
    act(() => server.receive(snapshot(readinessState())));

    await userEvent.click(screen.getByRole("button", { name: "Run all checks" }));
    const sent = server.sent.at(-1) as { module: string; command: string };
    expect([sent.module, sent.command]).toEqual(["readiness", "run"]);

    act(() =>
      server.receive(
        snapshot(
          readinessState({
            running: true,
            progress: { audio: { done: 1, total: 4 }, gateway: { done: 4, total: 4 } },
          }),
          2,
        ),
      ),
    );
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
    expect(screen.getByTestId("progress")).toHaveTextContent("audio 1/4");
    expect(screen.getByTestId("progress")).toHaveTextContent("gateway 4/4");
  });

  it("shows the banner and each check grouped by module, with the fix for problems", () => {
    const server = show(<ReadinessPage />);

    act(() => server.receive(snapshot(readinessState({ last_run: lastRun() }))));

    expect(screen.getByTestId("readiness-banner")).toHaveTextContent(
      "NOT READY (1 fail, 1 warning)",
    );
    expect(screen.getByRole("heading", { name: "Audio" })).toBeInTheDocument();
    const mixer = screen.getByTestId("check-audio.mixer");
    expect(mixer).toHaveClass("readiness-fail");
    expect(mixer).toHaveTextContent("XR18 answers");
    expect(mixer).toHaveTextContent("offline: no answer");
    expect(mixer).toHaveTextContent("Check the XR18's network cable.");
    // A fix that is made on a page links there.
    expect(within(mixer).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/audio/config",
    );
    expect(screen.getByTestId("check-audio.mvave")).toHaveClass("readiness-warn");
    // A passing check has nothing to fix.
    const caddy = screen.getByTestId("check-gateway.caddy");
    expect(caddy).toHaveClass("readiness-ok");
    expect(caddy.querySelector(".readiness-fix")).toBeNull();
    expect(screen.getByTestId("run-time")).toHaveTextContent("in 1.3 s");
  });

  it("shows READY in green when nothing fails", () => {
    const server = show(<ReadinessPage />);

    act(() => server.receive(snapshot(readinessState({ last_run: READY_RUN }))));

    const banner = screen.getByTestId("readiness-banner");
    expect(banner).toHaveTextContent("READY");
    expect(banner).toHaveClass("readiness-ready");
  });

  it("copies the problems as text for the crew chat", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const server = show(<ReadinessPage />);
    act(() => server.receive(snapshot(readinessState({ last_run: lastRun() }))));

    await userEvent.click(screen.getByRole("button", { name: "Copy as text" }));

    const text = (writeText.mock.calls[0] as unknown as [string])[0];
    const lines = text.split("\n");
    expect(lines[0]).toMatch(/^Baja Studio: NOT READY \(1 fail, 1 warning\) \(checked at /);
    // Failures first, each followed by its fix; passing checks are left out.
    expect(lines.slice(1)).toEqual([
      "FAIL Audio · XR18 answers: offline: no answer",
      "  → Check the XR18's network cable.",
      "WARN Audio · M-VAVE control surface: detail of mvave",
      "  → fix for mvave",
    ]);
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("says when scheduled runs are on and links to their setting when off", () => {
    const server = show(<ReadinessPage />);
    act(() => server.receive(snapshot(readinessState())));

    expect(screen.getByRole("link", { name: "settings" })).toHaveAttribute(
      "href",
      "/settings/readiness",
    );

    act(() =>
      server.receive(
        snapshot(
          readinessState({
            schedule: {
              enabled: true,
              interval_minutes: 5,
              next_run_at: "2026-11-07T15:05:00+00:00",
            },
          }),
          2,
        ),
      ),
    );
    expect(screen.getByTestId("schedule")).toHaveTextContent(/^Every 5 min, next at /);
  });
});

describe("Readiness toast", () => {
  const greenRun = { ...READY_RUN, trigger: "scheduled" as const };
  const redRun = lastRun({
    number: 2,
    trigger: "scheduled",
    newly_failed: ["audio.mixer"],
  });

  it("appears when a scheduled run turns a green check red", () => {
    const server = show(<ReadinessToast />);
    act(() => server.receive(snapshot(readinessState({ last_run: greenRun }))));
    expect(screen.queryByTestId("readiness-toast")).not.toBeInTheDocument();

    act(() => server.receive(snapshot(readinessState({ last_run: redRun }), 2)));

    const toast = screen.getByTestId("readiness-toast");
    expect(toast).toHaveTextContent("Readiness: a check went red");
    expect(toast).toHaveTextContent("XR18 answers: offline: no answer");
    expect(within(toast).getByRole("link", { name: "Open readiness" })).toHaveAttribute(
      "href",
      "/readiness",
    );
  });

  it("goes away when dismissed", async () => {
    const server = show(<ReadinessToast />);
    act(() => server.receive(snapshot(readinessState({ last_run: null }))));
    act(() => server.receive(snapshot(readinessState({ last_run: redRun }), 2)));

    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByTestId("readiness-toast")).not.toBeInTheDocument();
  });

  it("does not toast about a run that finished before the page opened", () => {
    const server = show(<ReadinessToast />);

    act(() => server.receive(snapshot(readinessState({ last_run: redRun }))));

    expect(screen.queryByTestId("readiness-toast")).not.toBeInTheDocument();
  });

  it("does not toast for a run someone started by hand", () => {
    const server = show(<ReadinessToast />);
    act(() => server.receive(snapshot(readinessState({ last_run: null }))));

    act(() =>
      server.receive(snapshot(readinessState({ last_run: { ...redRun, trigger: "manual" } }), 2)),
    );

    expect(screen.queryByTestId("readiness-toast")).not.toBeInTheDocument();
  });

  it("names every check that went red", () => {
    const server = show(<ReadinessToast />);
    act(() => server.receive(snapshot(readinessState({ last_run: null }))));
    const twoRed = {
      ...redRun,
      newly_failed: ["audio.mixer", "cameras.controller:desk"],
      modules: [
        ...redRun.modules,
        {
          name: "cameras",
          title: "Cameras",
          checks: [check("cameras", "controller:desk", "fail", { label: "Joystick desk" })],
        },
      ],
    };

    act(() => server.receive(snapshot(readinessState({ last_run: twoRed }), 2)));

    expect(screen.getByTestId("readiness-toast")).toHaveTextContent("2 checks went red");
    expect(screen.getByTestId("readiness-toast")).toHaveTextContent("Joystick desk");
  });
});
