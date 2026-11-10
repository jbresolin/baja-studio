/**
 * The messages card on the hosts screen (WP9 §4, WP9b §1), pinned under the grid.
 *
 * Compact at rest: one or two lines per message, small reactions, so the
 * cards above keep the screen. A message for this screen grows (large, in
 * the seat's colour, with the reactions, quick replies and a reply field)
 * while it waits for an answer, urgent ones pulsing, and shrinks back a few
 * seconds after it is answered (`isExpanded`). A message for a colleague is
 * one small line, so the host knows production is talking to the person
 * beside them. The ✕ hides a message on this screen for good: the server
 * keeps it per seat, so a reload does not bring it back. The host's own
 * messages to production show with production's answer.
 *
 * How much room the chat takes at rest is production's `chat_size`.
 */

import { useState } from "react";

import {
  addressedSeats,
  isExpanded,
  isFaded,
  isFromHost,
  isHiddenOn,
  isPulsing,
  messageColor,
  messageSize,
  senderSeat,
  senderTag,
  seatName,
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
  // Hidden at once on ✕, before the server's answer comes back.
  const [hiddenHere, setHiddenHere] = useState<Set<number>>(new Set());
  const shown = [...state.messages]
    .reverse()
    .filter((message) => !hiddenHere.has(message.id) && !isHiddenOn(message, screenSeats))
    .filter((message) => !isFromHost(message) || isOwn(message, screenSeats))
    .slice(0, MESSAGES_SHOWN);
  const growing = shown.some((message) => isExpanded(message, screenSeats, nowMs));
  const hide = (message: HostsMessage) => {
    const seatIds = isFromHost(message)
      ? [senderSeat(message)!]
      : addressedSeats(message, screenSeats);
    setHiddenHere((current) => new Set(current).add(message.id));
    void run("dismiss", { message_id: message.id, seat_ids: seatIds });
  };
  const size = state.config.chat_size;
  return (
    <div
      className={`hosts-chat hosts-chat-${size}${growing ? " hosts-chat-growing" : ""}`}
      data-testid="hosts-chat"
      data-chat-size={size}
      data-growing={growing}
    >
      {shown.length === 0 ? (
        <div className="hosts-quiet">No messages from production</div>
      ) : (
        <ul className="hosts-messages">
          {shown.map((message) => (
            <MessageItem
              key={message.id}
              message={message}
              state={state}
              screenSeats={screenSeats}
              nowMs={nowMs}
              run={run}
              onHide={() => hide(message)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function isOwn(message: HostsMessage, screenSeats: SeatId[]): boolean {
  const sender = senderSeat(message);
  return sender !== null && screenSeats.includes(sender);
}

interface MessageItemProps {
  message: HostsMessage;
  state: HostsState;
  screenSeats: SeatId[];
  nowMs: number;
  run: Run;
  onHide: () => void;
}

function MessageItem({ message, state, screenSeats, nowMs, run, onHide }: MessageItemProps) {
  const own = isFromHost(message);
  const size = own ? "large" : messageSize(message, screenSeats);
  const expanded = isExpanded(message, screenSeats, nowMs);
  const classes = [
    "hosts-message",
    `hosts-message-${size}`,
    `hosts-message-${message.kind}`,
    expanded ? "hosts-message-expanded" : "hosts-message-compact",
    isFaded(message, nowMs) ? "hosts-message-faded" : "",
    isPulsing(message) && !own ? "hosts-message-pulse" : "",
  ];
  const color = messageColor(message, state.seats);
  const answering = size === "large" && !own ? addressedSeats(message, screenSeats) : [];
  return (
    <li
      className={classes.filter(Boolean).join(" ")}
      style={{ borderColor: color }}
      data-testid={`hosts-message-${message.id}`}
      data-size={size}
      data-expanded={expanded}
    >
      <div className="hosts-message-head">
        {own ? (
          <span style={{ color }}>{seatName(senderSeat(message), state.seats)} → production</span>
        ) : (
          <span style={{ color }}>→ {targetName(message.to, state.seats)}</span>
        )}
        {!own && <span className="hosts-subtle">from {senderTag(message.from)}</span>}
        <span className="hosts-message-text">{message.text}</span>
        {size === "large" && (
          <button className="hosts-dismiss" aria-label="Dismiss" onClick={onHide}>
            ✕
          </button>
        )}
      </div>
      <Answers message={message} seats={state.seats} />
      {answering.map((seat) => (
        <ReactionRow
          key={seat}
          message={message}
          seat={state.seats.find((candidate) => candidate.id === seat)}
          seatId={seat}
          showSeatName={answering.length > 1}
          reactions={[...state.config.reactions, ...state.config.quick_replies]}
          withReplyField={expanded}
          run={run}
        />
      ))}
    </li>
  );
}

function Answers({ message, seats }: { message: HostsMessage; seats: Seat[] }) {
  if (message.reactions.length === 0 && message.reply_text === null) return null;
  const nameOf = (seatId: SeatId | null) =>
    seatId === null ? "production" : seatName(seatId, seats);
  return (
    <div className="hosts-subtle hosts-answers">
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
  /** Compact messages offer the taps only; the typed reply needs the grown message. */
  withReplyField: boolean;
  run: Run;
}

function ReactionRow({
  message,
  seat,
  seatId,
  showSeatName,
  reactions,
  withReplyField,
  run,
}: ReactionRowProps) {
  const [reply, setReply] = useState("");
  const sendReply = () => {
    const text = reply.trim();
    if (!text) return;
    void run("reply", { message_id: message.id, seat_id: seatId, text });
    setReply("");
  };
  return (
    <div className={withReplyField ? "hosts-reactions" : "hosts-reactions hosts-reactions-compact"}>
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
      {withReplyField && (
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
      )}
    </div>
  );
}
