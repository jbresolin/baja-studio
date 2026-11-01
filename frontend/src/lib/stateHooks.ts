/**
 * React hooks over the shared `StateSocket`.
 *
 * Components read live state with `useModuleState("heartbeat")` and send
 * commands with `useSendCommand()`. The socket comes from
 * `StateSocketContext`, so tests can provide one connected to a fake.
 */

import { createContext, useCallback, useContext, useSyncExternalStore } from "react";

import type { ConnectionStatus, StateSocket } from "./stateSocket";

export const StateSocketContext = createContext<StateSocket | null>(null);

/** The socket provided by the nearest `StateSocketContext`. */
export function useStateSocket(): StateSocket {
  const socket = useContext(StateSocketContext);
  if (socket === null) throw new Error("useStateSocket needs a StateSocketContext provider");
  return socket;
}

/** One module's live state subtree, re-rendering whenever it changes. */
export function useModuleState<T = Record<string, unknown>>(module: string): T | undefined {
  const socket = useStateSocket();
  return useSyncExternalStore(
    (listener) => socket.subscribe(listener),
    () => socket.getState()[module] as T | undefined,
  );
}

/** "connecting", "open" or "closed", for the connection indicator. */
export function useConnectionStatus(): ConnectionStatus {
  const socket = useStateSocket();
  return useSyncExternalStore(
    (listener) => socket.subscribe(listener),
    () => socket.getStatus(),
  );
}

/** A function that sends a command over the socket and resolves with the reply. */
export function useSendCommand(): (
  module: string,
  command: string,
  args?: Record<string, unknown>,
) => Promise<unknown> {
  const socket = useStateSocket();
  return useCallback(
    (module, command, args = {}) => socket.sendCommand(module, command, args),
    [socket],
  );
}
