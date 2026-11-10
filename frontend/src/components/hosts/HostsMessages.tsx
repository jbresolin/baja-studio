/**
 * The messages card on the hosts screen (WP9 §4), pinned under the grid.
 *
 * The newest messages first. A message for one of this screen's seats (or
 * for everyone) is large, in the seat's colour, with the reactions, the
 * quick replies and a small reply field; a message for a colleague is
 * small, so the host knows production is talking to the person beside them.
 * Urgent messages pulse until someone reacts; answered, dismissed and
 * expired ones fade.
 */

import { useState } from "react";

import {
  addressedSeats,
  isFaded,
  isPulsing,
  messageSize,
  senderTag,
  targetColor,
  targetName,
  type HostsMessage,
  type HostsState,
  type Seat,
  type SeatId,
} from "../../lib/hosts";

const MESSAGES_SHOWN = 6;

type Run = (command: string, args: Record<string, unknown>) => Promise<unknown>;

interface HostsMessagesProps {
  state: HostsState;
  screenSeats: SeatId[];
  nowMs: number;
  run: Run;
}

export function HostsMessages({ state, screenSeats, nowMs, run }: HostsMessagesProps) {
  const newestFirst = [...state.messages].reverse().slice(0, MESSAGES_SHOWN);
  if (newestFirst.length === 0) {
    return <div className="hosts-quiet">No messages from production</div>;
  }
  return (
    <ul className="hosts-messages">
      {newestFirst.map((message) => (
        <MessageItem
          key={message.id}
          message={message}
          state={state}
          screenSeats={screenSeats}
          nowMs={nowMs}
          run={run}
        />
      ))}
    </ul>
  );
}

interface MessageItemProps {
  message: HostsMessage;
  state: HostsState;
  screenSeats: SeatId[];
  nowMs: number;
  run: Run;
}

function MessageItem({ message, state, screenSeats, nowMs, run }: MessageItemProps) {
  const size = messageSize(message, screenSeats);
  const faded = isFaded(message, nowMs);
  const classes = [
    "hosts-message",
    `hosts-message-${size}`,
    `hosts-message-${message.kind}`,
    faded ? "hosts-message-faded" : "",
    isPulsing(message) ? "hosts-message-pulse" : "",
  ];
  const color = targetColor(message.to, state.seats);
  const answering = size === "large" ? addressedSeats(message, screenSeats) : [];
  return (
    <li
      className={classes.filter(Boolean).join(" ")}
      style={{ borderColor: color }}
      data-testid={`hosts-message-${message.id}`}
      data-size={size}
    >
      <div className="hosts-message-head">
        <span style={{ color }}>→ {targetName(message.to, state.seats)}</span>
        <span className="hosts-subtle">from {senderTag(message.from)}</span>
        {size === "large" && message.dismissed_at === null && (
          <button
            className="hosts-dismiss"
            aria-label="Dismiss"
            onClick={() => void run("dismiss", { message_id: message.id })}
          >
            ✕
          </button>
        )}
      </div>
      <div className="hosts-message-text">{message.text}</div>
      <Answers message={message} seats={state.seats} />
      {answering.map((seat) => (
        <ReactionRow
          key={seat}
          message={message}
          seat={state.seats.find((candidate) => candidate.id === seat)}
          seatId={seat}
          showSeatName={answering.length > 1}
          reactions={[...state.config.reactions, ...state.config.quick_replies]}
          run={run}
        />
      ))}
    </li>
  );
}

function Answers({ message, seats }: { message: HostsMessage; seats: Seat[] }) {
  if (message.reactions.length === 0 && message.reply_text === null) return null;
  const nameOf = (seatId: SeatId | null) =>
    seats.find((seat) => seat.id === seatId)?.screen_name ?? `seat ${seatId}`;
  return (
    <div className="hosts-subtle">
      {message.reactions.map((reaction, index) => (
        <span key={index}>
          {reaction.reaction} {nameOf(reaction.seat_id)}{" "}
        </span>
      ))}
      {message.reply_text !== null && (
        <span>
          “{message.reply_text}” — {nameOf(message.reply_seat_id)}
        </span>
      )}
    </div>
  );
}

interface ReactionRowProps {
  message: HostsMessage;
  seat: Seat | undefined;
  seatId: SeatId;
  showSeatName: boolean;
  reactions: string[];
  run: Run;
}

function ReactionRow({ message, seat, seatId, showSeatName, reactions, run }: ReactionRowProps) {
  const [reply, setReply] = useState("");
  const sendReply = () => {
    const text = reply.trim();
    if (!text) return;
    void run("reply", { message_id: message.id, seat_id: seatId, text });
    setReply("");
  };
  return (
    <div className="hosts-reactions">
      {showSeatName && (
        <span style={{ color: seat?.color }}>{seat?.screen_name ?? `Seat ${seatId}`}</span>
      )}
      {reactions.map((reaction) => (
        <button
          key={reaction}
          className="hosts-reaction"
          onClick={() => void run("react", { message_id: message.id, seat_id: seatId, reaction })}
        >
          {reaction}
        </button>
      ))}
      <input
        className="hosts-reply"
        aria-label="Reply"
        placeholder="reply…"
        maxLength={200}
        value={reply}
        onChange={(event) => setReply(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") sendReply();
        }}
      />
    </div>
  );
}
