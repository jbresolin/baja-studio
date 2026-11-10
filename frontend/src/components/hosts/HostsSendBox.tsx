/**
 * "Message hosts" on the playout page (WP9 §5): the compact composer, so the
 * operator can cue the hosts without leaving playout. Shows nothing while
 * the hosts module is off.
 *
 * WP9b: when a host has written and production has not read it yet, the
 * line shows how many and the latest one in the seat's colour, with "Read"
 * (the Hosts page has the conversation and the answers).
 */

import { Link } from "react-router-dom";

import {
  messageColor,
  senderSeat,
  seatName,
  unreadFromHosts,
  type HostsState,
} from "../../lib/hosts";
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
  const unread = unreadFromHosts(state.messages);
  const latest = unread.at(-1);
  return (
    <div className="hosts-send-box">
      <MessageComposer state={state} run={run} compact />
      {latest && (
        <div className="hosts-unread-line" role="status" data-testid="hosts-unread">
          <span className="badge badge-unread">{unread.length}</span>{" "}
          <span style={{ color: messageColor(latest, state.seats) }}>
            {seatName(senderSeat(latest), state.seats)}:
          </span>{" "}
          <strong>{latest.text}</strong> <Link to="/hosts/control">answer</Link>{" "}
          <button
            onClick={() =>
              void run("mark_read", { message_ids: unread.map((message) => message.id) })
            }
          >
            Read
          </button>
        </div>
      )}
    </div>
  );
}
