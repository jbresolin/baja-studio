/**
 * Production's view of the conversation (WP9 §4, WP9b §1): newest first.
 *
 * Production's messages show their delivery (shown on which seat's screen),
 * the hosts' reactions and reply as they arrive, which seats hid them, and
 * Dismiss. A host's message shows in the seat's colour, marked unread until
 * this page has shown it, with reactions and a reply field for production.
 * Answered, dismissed and expired messages fade, as on the hosts screen.
 */

import { useState } from "react";

import {
  isFaded,
  isFromHost,
  messageColor,
  senderSeat,
  senderTag,
  seatName,
  targetName,
  type HostsMessage,
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
    seatId === null ? "production" : seatName(seatId, state.seats);
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
        {newestFirst.map((message) => {
          const fromHost = isFromHost(message);
          const classes = ["hosts-history-item"];
          if (isFaded(message, nowMs)) classes.push("faded");
          if (fromHost) classes.push("hosts-history-from-host");
          return (
            <li
              key={message.id}
              className={classes.join(" ")}
              style={fromHost ? { borderColor: messageColor(message, state.seats) } : undefined}
              data-testid={`history-${message.id}`}
            >
              <div>
                {fromHost ? (
                  <span style={{ color: messageColor(message, state.seats) }}>
                    ← {nameOf(senderSeat(message))}
                  </span>
                ) : (
                  <span style={{ color: messageColor(message, state.seats) }}>
                    → {targetName(message.to, state.seats)}
                  </span>
                )}{" "}
                <span className={`badge hosts-kind-${message.kind}`}>{message.kind}</span>{" "}
                {fromHost && message.read_at === null && (
                  <span className="badge badge-unread" data-testid={`unread-${message.id}`}>
                    new
                  </span>
                )}{" "}
                <strong>{message.text}</strong>{" "}
                <span className="muted">
                  {new Date(message.ts).toLocaleTimeString()}
                  {fromHost ? "" : ` · ${senderTag(message.from)}`}
                </span>
              </div>
              {!fromHost && (
                <div className="muted" data-testid={`delivery-${message.id}`}>
                  {message.shown.length === 0
                    ? "not shown yet"
                    : `shown: ${message.shown.map((entry) => nameOf(entry.seat_id)).join(", ")}`}
                </div>
              )}
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
              {message.dismissed_by.length > 0 && (
                <div className="muted" data-testid={`dismissed-by-${message.id}`}>
                  dismissed by{" "}
                  {message.dismissed_by.map((entry) => nameOf(entry.seat_id)).join(", ")}
                </div>
              )}
              {fromHost ? (
                <AnswerHost message={message} reactions={state.config.reactions} run={run} />
              ) : (
                message.dismissed_at === null && (
                  <button onClick={() => void run("dismiss", { message_id: message.id })}>
                    Dismiss
                  </button>
                )
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Production reacts or replies to a host's message, with the hosts' own commands. */
function AnswerHost({
  message,
  reactions,
  run,
}: {
  message: HostsMessage;
  reactions: string[];
  run: Run;
}) {
  const [reply, setReply] = useState("");
  const send = () => {
    const text = reply.trim();
    if (!text) return;
    void run("reply", { message_id: message.id, text });
    setReply("");
  };
  return (
    <div className="button-row">
      {reactions.map((reaction) => (
        <button
          key={reaction}
          aria-label={`React ${reaction} to message ${message.id}`}
          onClick={() => void run("react", { message_id: message.id, reaction })}
        >
          {reaction}
        </button>
      ))}
      <input
        aria-label={`Reply to message ${message.id}`}
        placeholder="reply…"
        maxLength={200}
        value={reply}
        onChange={(event) => setReply(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") send();
        }}
      />
    </div>
  );
}
