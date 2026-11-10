/**
 * The Hosts page (/hosts/control): production's side of the hosts screen
 * (WP9 §4, §5). The seat strip (who is online), the composer with canned
 * messages, the conversation with delivery and reactions, the card layout
 * with Push, and the seats and texts setup.
 *
 * Every change is a command to the `hosts` module; the server keeps all the
 * state, so two producers see the same conversation.
 *
 * WP9b: messages the hosts send to production show in the history in the
 * seat's colour, with an unread count by the title (cleared with "Mark
 * read" or by answering) and a chime for each new one when the
 * `production_chime` setting is on (default on: production is not on IEMs).
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { LayoutEditor } from "../components/hosts/LayoutEditor";
import { MessageComposer } from "../components/hosts/MessageComposer";
import { MessageHistory } from "../components/hosts/MessageHistory";
import { SeatsEditor } from "../components/hosts/SeatsEditor";
import { TextsEditor } from "../components/hosts/TextsEditor";
import { playChime } from "../lib/chime";
import { isFromHost, unreadFromHosts, type HostsState } from "../lib/hosts";
import { useModuleState, useSendCommand } from "../lib/stateHooks";

const ERROR_SHOWN_MS = 4000;

export function HostsControlPage() {
  const state = useModuleState<HostsState>("hosts");
  const sendCommand = useSendCommand();
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (error === null) return;
    const timer = setTimeout(() => setError(null), ERROR_SHOWN_MS);
    return () => clearTimeout(timer);
  }, [error]);

  const run = async (command: string, args: Record<string, unknown> = {}) => {
    try {
      return await sendCommand("hosts", command, args);
    } catch (failure) {
      setError((failure as Error).message);
      return undefined;
    }
  };

  useProductionChime(state);

  if (!state) return <p className="muted">Waiting for the studio service…</p>;
  const unread = unreadFromHosts(state.messages);

  return (
    <div className="hosts-control">
      <div className="page-header">
        <h1>Hosts</h1>
        {unread.length > 0 && (
          <>
            <span className="badge badge-unread" data-testid="unread-count">
              {unread.length} new from the hosts
            </span>
            <button
              onClick={() =>
                void run("mark_read", { message_ids: unread.map((message) => message.id) })
              }
            >
              Mark read
            </button>
          </>
        )}
        <Link to="/hosts" target="_blank">
          Open the hosts screen
        </Link>
      </div>
      <div className="hosts-seat-strip" aria-label="Seat strip">
        {state.seats
          .filter((seat) => seat.enabled)
          .map((seat) => (
            <span
              key={seat.id}
              className="hosts-seat-chip"
              style={{ borderColor: seat.color, color: seat.color }}
              data-testid={`seat-chip-${seat.id}`}
            >
              {seat.screen_name || `Seat ${seat.id}`}{" "}
              <span className={seat.online ? "badge badge-ok" : "badge badge-offline"}>
                {seat.online ? "online" : "offline"}
              </span>
            </span>
          ))}
      </div>
      <div className="hosts-control-columns">
        <div>
          <MessageComposer state={state} run={run} />
          <MessageHistory state={state} nowMs={nowMs} run={run} />
        </div>
        <div>
          <LayoutEditor state={state} run={run} />
        </div>
      </div>
      <SeatsEditor state={state} run={run} />
      <TextsEditor state={state} run={run} />
      {error && (
        <div className="toast toast-error" role="status">
          {error}
        </div>
      )}
    </div>
  );
}

/** A chime for each new message from a host while this page is open, when production wants it. */
function useProductionChime(state: HostsState | undefined) {
  const newestSeen = useRef<number | null>(null);
  useEffect(() => {
    if (!state) return;
    const newest = state.messages.at(-1)?.id ?? 0;
    const previous = newestSeen.current;
    newestSeen.current = newest;
    // Opening the page is not news: only messages after that chime.
    if (previous === null || !state.config.production_chime) return;
    if (state.messages.some((message) => message.id > previous && isFromHost(message))) {
      playChime();
    }
  }, [state]);
}
