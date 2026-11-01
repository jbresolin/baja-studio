/**
 * A stand-in for the browser's WebSocket, so tests can play the server.
 *
 * The test decides when the connection "opens", what the "server" sends,
 * and when the connection drops, and can read what the page sent.
 */

import type { SocketLike } from "../src/lib/stateSocket";

export class FakeSocket implements SocketLike {
  readyState = 0; // 0 = still connecting, like a real WebSocket
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  sent: unknown[] = [];
  closed = false;

  constructor(readonly url: string) {}

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  close(): void {
    this.closed = true;
    this.readyState = 3;
  }

  /** Pretend the server accepted the connection. */
  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  /** Pretend the server sent `message`. */
  receive(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  /** Pretend the connection dropped (server restarted, Wi-Fi blip...). */
  drop(): void {
    this.readyState = 3;
    this.onclose?.();
  }
}

/** Creates FakeSockets and remembers each one, newest last. */
export function fakeSocketFactory() {
  const created: FakeSocket[] = [];
  return {
    created,
    create: (url: string) => {
      const socket = new FakeSocket(url);
      created.push(socket);
      return socket;
    },
    latest: () => created[created.length - 1]!,
  };
}
