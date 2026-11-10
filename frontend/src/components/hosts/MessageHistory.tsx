/**
 * Production's view of the conversation (WP9 §4): newest first, each
 * message with its delivery (shown on which seat's screen), the reactions
 * and reply as they arrive, and Dismiss. Answered, dismissed and expired
 * messages fade, as on the hosts screen.
 */

import {
  isFaded,
  senderTag,
  targetColor,
  targetName,
  type HostsState,
  type SeatId,
} from "../../lib/hosts";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface MessageHistoryProps {
  state: HostsState;
  nowMs: number;
  run: Run;
}

export function MessageHistory({ state, nowMs, run }: MessageHistoryProps) {
  const nameOf = (seatId: SeatId | null) =>
    state.seats.find((seat) => seat.id === seatId)?.screen_name ?? `seat ${seatId}`;
  const newestFirst = [...state.messages].reverse();
  return (
    <section aria-label="Message history">
      <div className="playout-toolbar">
        <h2>History</h2>
        <button
          disabled={state.messages.length === 0}
          onClick={() => {
            if (window.confirm("Clear every message of this session?")) void run("clear_messages");
          }}
        >
          Clear history
        </button>
      </div>
      {newestFirst.length === 0 && <p className="muted">No messages yet.</p>}
      <ul className="hosts-history">
        {newestFirst.map((message) => (
          <li
            key={message.id}
            className={isFaded(message, nowMs) ? "hosts-history-item faded" : "hosts-history-item"}
            data-testid={`history-${message.id}`}
          >
            <div>
              <span style={{ color: targetColor(message.to, state.seats) }}>
                → {targetName(message.to, state.seats)}
              </span>{" "}
              <span className={`badge hosts-kind-${message.kind}`}>{message.kind}</span>{" "}
              <strong>{message.text}</strong>{" "}
              <span className="muted">
                {new Date(message.ts).toLocaleTimeString()} · {senderTag(message.from)}
              </span>
            </div>
            <div className="muted" data-testid={`delivery-${message.id}`}>
              {message.shown.length === 0
                ? "not shown yet"
                : `shown: ${message.shown.map((entry) => nameOf(entry.seat_id)).join(", ")}`}
            </div>
            {message.reactions.length > 0 && (
              <div data-testid={`reactions-${message.id}`}>
                {message.reactions.map((reaction, index) => (
                  <span key={index} className="hosts-history-reaction">
                    {reaction.reaction} {nameOf(reaction.seat_id)}
                  </span>
                ))}
              </div>
            )}
            {message.reply_text !== null && (
              <div>
                “{message.reply_text}” — {nameOf(message.reply_seat_id)}
              </div>
            )}
            {message.dismissed_at === null && (
              <button onClick={() => void run("dismiss", { message_id: message.id })}>
                Dismiss
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
