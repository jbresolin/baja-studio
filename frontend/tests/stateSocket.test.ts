/**
 * Tests for the live-state WebSocket client (src/lib/stateSocket.ts).
 *
 * We never open a real connection: a FakeSocket plays the server, so each
 * test can send exactly the messages it wants and check how the client reacts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applyPatch, StateSocket } from "../src/lib/stateSocket";
import { fakeSocketFactory } from "./fakeSocket";

function connectedClient() {
  const sockets = fakeSocketFactory();
  const client = new StateSocket({
    url: "ws://studio/ws",
    createSocket: sockets.create,
    firstRetryDelayMs: 500,
    maxRetryDelayMs: 4000,
  });
  client.connect();
  sockets.latest().open();
  return { client, sockets };
}

describe("applyPatch", () => {
  it("sets a nested value and creates missing objects on the way", () => {
    const tree = { heartbeat: { count: 1 } };

    const updated = applyPatch(tree, "camera", "position.pan", 10);

    expect(updated).toEqual({ heartbeat: { count: 1 }, camera: { position: { pan: 10 } } });
  });

  it("replaces the whole module subtree when the path is empty", () => {
    expect(applyPatch({ echo: { a: 1 } }, "echo", "", { b: 2 })).toEqual({ echo: { b: 2 } });
  });

  it("does not modify the original tree and keeps untouched parts as the same objects", () => {
    // React decides what to re-render by comparing object identity, so an
    // unchanged module must be the very same object after a patch elsewhere.
    const heartbeat = { count: 1 };
    const tree = { heartbeat, echo: { last_reply: null } };

    const updated = applyPatch(tree, "echo", "last_reply", "HI");

    expect(tree.echo.last_reply).toBeNull();
    expect(updated.heartbeat).toBe(heartbeat);
  });
});

describe("StateSocket", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("takes the snapshot as its state and applies following patches", () => {
    const { client, sockets } = connectedClient();

    sockets.latest().receive({ type: "snapshot", seq: 4, state: { heartbeat: { count: 1 } } });
    sockets
      .latest()
      .receive({ type: "patch", module: "heartbeat", path: "count", value: 2, seq: 5 });

    expect(client.getState()).toEqual({ heartbeat: { count: 2 } });
  });

  it("tells subscribers about every change", () => {
    const { client, sockets } = connectedClient();
    const listener = vi.fn();
    client.subscribe(listener);

    sockets.latest().receive({ type: "snapshot", seq: 0, state: {} });

    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("asks for a new snapshot when a patch number is skipped", () => {
    const { client, sockets } = connectedClient();
    const server = sockets.latest();
    server.receive({ type: "snapshot", seq: 10, state: { heartbeat: { count: 1 } } });

    // Patch 11 never arrived; 12 shows up. Applying it could leave us with
    // wrong state, so the client must ignore it and ask for a snapshot.
    server.receive({ type: "patch", module: "heartbeat", path: "count", value: 3, seq: 12 });

    expect(server.sent).toEqual([{ type: "snapshot_request" }]);
    expect(client.getState()).toEqual({ heartbeat: { count: 1 } });

    // Patches arriving before the snapshot are also ignored...
    server.receive({ type: "patch", module: "heartbeat", path: "count", value: 4, seq: 13 });
    expect(client.getState()).toEqual({ heartbeat: { count: 1 } });

    // ...and the snapshot puts everything right.
    server.receive({ type: "snapshot", seq: 13, state: { heartbeat: { count: 4 } } });
    expect(client.getState()).toEqual({ heartbeat: { count: 4 } });
  });

  it("ignores patches already included in the snapshot", () => {
    const { client, sockets } = connectedClient();
    sockets.latest().receive({ type: "snapshot", seq: 7, state: { heartbeat: { count: 9 } } });

    sockets
      .latest()
      .receive({ type: "patch", module: "heartbeat", path: "count", value: 8, seq: 7 });

    expect(client.getState()).toEqual({ heartbeat: { count: 9 } });
    expect(sockets.latest().sent).toEqual([]);
  });

  it("reconnects after the connection drops, waiting longer after each failure", () => {
    const { client, sockets } = connectedClient();
    expect(client.getStatus()).toBe("open");

    // The server goes away. The client should show "connecting" and try
    // again after 500 ms...
    sockets.latest().drop();
    expect(client.getStatus()).toBe("connecting");
    vi.advanceTimersByTime(499);
    expect(sockets.created).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets.created).toHaveLength(2);

    // ...that attempt fails too, so the next wait is twice as long (1000 ms).
    sockets.latest().drop();
    vi.advanceTimersByTime(999);
    expect(sockets.created).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets.created).toHaveLength(3);

    // Once a connection succeeds, the wait goes back to 500 ms.
    sockets.latest().open();
    sockets.latest().drop();
    vi.advanceTimersByTime(500);
    expect(sockets.created).toHaveLength(4);
  });

  it("never waits longer than the maximum retry delay", () => {
    const { sockets } = connectedClient();

    // Fail five times in a row: 500, 1000, 2000, 4000, then capped at 4000.
    for (const delay of [500, 1000, 2000, 4000, 4000]) {
      const before = sockets.created.length;
      sockets.latest().drop();
      vi.advanceTimersByTime(delay);
      expect(sockets.created).toHaveLength(before + 1);
    }
  });

  it("stops reconnecting after close()", () => {
    const { client, sockets } = connectedClient();

    client.close();
    vi.advanceTimersByTime(60_000);

    expect(sockets.created).toHaveLength(1);
    expect(sockets.latest().closed).toBe(true);
    expect(client.getStatus()).toBe("closed");
  });

  it("resolves a command with the result of its reply", async () => {
    const { client, sockets } = connectedClient();

    const reply = client.sendCommand("echo", "say", { text: "hi" });
    const sent = sockets.latest().sent[0] as { request_id: string };
    expect(sent).toMatchObject({ module: "echo", command: "say", args: { text: "hi" } });
    sockets
      .latest()
      .receive({ type: "reply", request_id: sent.request_id, ok: true, result: "HI" });

    await expect(reply).resolves.toBe("HI");
  });

  it("rejects a command with the server's error message", async () => {
    const { client, sockets } = connectedClient();

    const reply = client.sendCommand("heartbeat", "fly");
    const sent = sockets.latest().sent[0] as { request_id: string };
    sockets.latest().receive({
      type: "reply",
      request_id: sent.request_id,
      ok: false,
      error: "no command 'fly'",
    });

    await expect(reply).rejects.toThrow("no command 'fly'");
  });

  it("rejects waiting commands when the connection drops", async () => {
    const { client, sockets } = connectedClient();

    const reply = client.sendCommand("echo", "say", { text: "hi" });
    sockets.latest().drop();

    await expect(reply).rejects.toThrow("connection lost");
  });

  it("refuses to send a command while not connected", async () => {
    const sockets = fakeSocketFactory();
    const client = new StateSocket({ url: "ws://studio/ws", createSocket: sockets.create });
    client.connect(); // never opened

    await expect(client.sendCommand("echo", "say")).rejects.toThrow("not connected");
  });
});
