/**
 * WebSocket client for the service's live state.
 *
 * The server sends a full snapshot on connect, then numbered patches
 * `{module, path, value, seq}`. If a patch number is skipped we ask for a new
 * snapshot. Commands go out as `{module, command, args, request_id}` and the
 * matching reply resolves the promise returned by `sendCommand`.
 *
 * Each snapshot carries this page's `client_id`: the server logs the page's
 * commands as `web:<client id>`. On reconnect the page asks for the same id
 * again (`?client=`), so it stays the same source (and keeps owning the camera
 * it was moving) across a Wi-Fi blip.
 */

export type StateTree = Record<string, unknown>;
export type ConnectionStatus = "connecting" | "open" | "closed";

interface SnapshotMessage {
  type: "snapshot";
  seq: number;
  state: StateTree;
  client_id?: string;
}

interface PatchMessage {
  type: "patch";
  module: string;
  path: string;
  value: unknown;
  seq: number;
}

interface ReplyMessage {
  type: "reply";
  request_id: string;
  ok: boolean;
  result?: unknown;
  error?: string;
}

type ServerMessage = SnapshotMessage | PatchMessage | ReplyMessage;

/** The parts of the browser WebSocket we use; tests pass a fake with the same shape. */
export interface SocketLike {
  readyState: number;
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: (() => void) | null;
  send(data: string): void;
  close(): void;
}

export interface StateSocketOptions {
  url: string;
  createSocket?: (url: string) => SocketLike;
  firstRetryDelayMs?: number;
  maxRetryDelayMs?: number;
  /** The id this page had before a reload, to ask for again. */
  clientId?: string;
  /** Called when the server gives the page its id, so it can be kept across reloads. */
  onClientId?: (clientId: string) => void;
}

const SOCKET_OPEN = 1;

/**
 * Return a new tree with `value` set at `path` inside `module`'s subtree.
 *
 * Objects along the path are copied, everything else is shared, so React can
 * tell what changed by comparing references.
 */
export function applyPatch(
  tree: StateTree,
  module: string,
  path: string,
  value: unknown,
): StateTree {
  const keys = path ? path.split(".").filter(Boolean) : [];
  if (keys.length === 0) {
    return { ...tree, [module]: value };
  }
  return { ...tree, [module]: setIn(tree[module], keys, value) };
}

function setIn(node: unknown, keys: string[], value: unknown): unknown {
  const [first, ...rest] = keys as [string, ...string[]];
  const current = isPlainObject(node) ? node : {};
  return { ...current, [first]: rest.length === 0 ? value : setIn(current[first], rest, value) };
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The URL of the service's WebSocket, on the same host that served the page. */
export function defaultSocketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

/** Keeps the state tree in sync with the server and reconnects when the link drops. */
export class StateSocket {
  private readonly url: string;
  private readonly createSocket: (url: string) => SocketLike;
  private readonly firstRetryDelayMs: number;
  private readonly maxRetryDelayMs: number;
  private socket: SocketLike | null = null;
  private state: StateTree = {};
  private status: ConnectionStatus = "closed";
  private lastSeq: number | null = null;
  private waitingForSnapshot = false;
  private nextRetryDelayMs: number;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  private nextRequestNumber = 1;
  private clientId: string | null;
  private readonly onClientId: (clientId: string) => void;
  private readonly listeners = new Set<() => void>();
  private readonly pendingCommands = new Map<
    string,
    { resolve: (result: unknown) => void; reject: (error: Error) => void }
  >();

  constructor(options: StateSocketOptions) {
    this.url = options.url;
    this.createSocket = options.createSocket ?? ((url) => new WebSocket(url) as SocketLike);
    this.firstRetryDelayMs = options.firstRetryDelayMs ?? 500;
    this.maxRetryDelayMs = options.maxRetryDelayMs ?? 10_000;
    this.nextRetryDelayMs = this.firstRetryDelayMs;
    this.clientId = options.clientId ?? null;
    this.onClientId = options.onClientId ?? (() => undefined);
  }

  /** Open the connection (and keep reopening it) until `close` is called. */
  connect(): void {
    this.stopped = false;
    this.openSocket();
  }

  /** Close for good: no more reconnects, pending commands fail. */
  close(): void {
    this.stopped = true;
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.socket?.close();
    this.socket = null;
    this.setStatus("closed");
    this.failPendingCommands("connection closed");
  }

  /** The current state tree; the same object until something changes. */
  getState(): StateTree {
    return this.state;
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  /** The id the server logs this page's commands under (`web:<id>`), once known. */
  getClientId(): string | null {
    return this.clientId;
  }

  /** Call `listener` after every state or status change; returns an unsubscribe function. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Send a command to a module; resolves with its result or rejects with its error. */
  sendCommand(
    module: string,
    command: string,
    args: Record<string, unknown> = {},
  ): Promise<unknown> {
    if (this.socket === null || this.socket.readyState !== SOCKET_OPEN) {
      return Promise.reject(new Error("not connected to the studio service"));
    }
    const requestId = `r${this.nextRequestNumber++}`;
    const reply = new Promise<unknown>((resolve, reject) => {
      this.pendingCommands.set(requestId, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ module, command, args, request_id: requestId }));
    return reply;
  }

  private openSocket(): void {
    this.setStatus("connecting");
    const url = this.clientId
      ? `${this.url}?client=${encodeURIComponent(this.clientId)}`
      : this.url;
    const socket = this.createSocket(url);
    this.socket = socket;
    socket.onopen = () => {
      this.nextRetryDelayMs = this.firstRetryDelayMs;
      this.setStatus("open");
    };
    socket.onmessage = (event) => this.handleMessage(JSON.parse(event.data) as ServerMessage);
    socket.onclose = () => this.handleClose(socket);
  }

  private handleClose(socket: SocketLike): void {
    if (socket !== this.socket) return; // an old socket we already replaced
    this.socket = null;
    this.lastSeq = null;
    this.failPendingCommands("connection lost");
    if (this.stopped) return;
    this.setStatus("connecting");
    // Wait a little longer after each failed attempt so a down server is not hammered.
    const delay = this.nextRetryDelayMs;
    this.nextRetryDelayMs = Math.min(this.nextRetryDelayMs * 2, this.maxRetryDelayMs);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (!this.stopped) this.openSocket();
    }, delay);
  }

  private handleMessage(message: ServerMessage): void {
    switch (message.type) {
      case "snapshot":
        if (message.client_id && message.client_id !== this.clientId) {
          this.clientId = message.client_id;
          this.onClientId(message.client_id);
        }
        this.state = message.state;
        this.lastSeq = message.seq;
        this.waitingForSnapshot = false;
        this.notify();
        return;
      case "patch":
        this.handlePatch(message);
        return;
      case "reply":
        this.handleReply(message);
        return;
    }
  }

  private handlePatch(patch: PatchMessage): void {
    if (this.waitingForSnapshot) return; // the coming snapshot will include this change
    if (this.lastSeq !== null && patch.seq <= this.lastSeq) return; // already in our snapshot
    if (this.lastSeq !== null && patch.seq !== this.lastSeq + 1) {
      // We missed at least one patch, so our tree may be wrong: start again from a snapshot.
      this.waitingForSnapshot = true;
      this.socket?.send(JSON.stringify({ type: "snapshot_request" }));
      return;
    }
    this.state = applyPatch(this.state, patch.module, patch.path, patch.value);
    this.lastSeq = patch.seq;
    this.notify();
  }

  private handleReply(reply: ReplyMessage): void {
    const pending = this.pendingCommands.get(reply.request_id);
    if (pending === undefined) return;
    this.pendingCommands.delete(reply.request_id);
    if (reply.ok) pending.resolve(reply.result);
    else pending.reject(new Error(reply.error ?? "command failed"));
  }

  private failPendingCommands(reason: string): void {
    for (const pending of this.pendingCommands.values()) pending.reject(new Error(reason));
    this.pendingCommands.clear();
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.notify();
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}
