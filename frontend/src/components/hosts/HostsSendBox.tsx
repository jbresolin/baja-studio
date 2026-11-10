/**
 * "Message hosts" on the playout page (WP9 §5): the compact composer, so the
 * operator can cue the hosts without leaving playout. Shows nothing while
 * the hosts module is off.
 */

import type { HostsState } from "../../lib/hosts";
import { useModuleState, useSendCommand } from "../../lib/stateHooks";

import { MessageComposer } from "./MessageComposer";

interface HostsSendBoxProps {
  /** Where a refusal goes (the playout page's toast). */
  onError: (message: string) => void;
}

export function HostsSendBox({ onError }: HostsSendBoxProps) {
  const state = useModuleState<HostsState>("hosts");
  const sendCommand = useSendCommand();
  if (!state || state.seats.length === 0) return null;
  const run = async (command: string, args: Record<string, unknown>) => {
    try {
      return await sendCommand("hosts", command, args);
    } catch (failure) {
      onError((failure as Error).message);
      return undefined;
    }
  };
  return <MessageComposer state={state} run={run} compact />;
}
