/**
 * Types and pure helpers for the hosts screen and its control page (WP9, WP9b).
 *
 * Shapes mirror the server: `hosts` state from
 * server/baja_studio/modules/hosts/desk.py, messages from
 * server/baja_studio/modules/hosts/messages.py, cards from
 * server/baja_studio/core/hosts_cards.py.
 */

export type SeatId = 1 | 2 | 3 | 4;
/** Production writes to a seat or to all; a host writes to "production" only. */
export type MessageTarget = SeatId | "all" | "production";
export type MessageKind = "info" | "question" | "urgent";
export type ChatSize = "small" | "medium" | "large";

export interface Seat {
  id: SeatId;
  screen_name: string;
  color: string;
  enabled: boolean;
  online: boolean;
  screens: number;
}

export interface HostsMessage {
  id: number;
  ts: string;
  /** "production:<client id>" or "seat:<id>". */
  from: string;
  to: MessageTarget;
  text: string;
  kind: MessageKind;
  expires_s: number | null;
  expires_at: string | null;
  /** `seat_id` null: production answering a host's message (`by` names the page). */
  reactions: { seat_id: SeatId | null; reaction: string; ts: string; by?: string }[];
  reply_text: string | null;
  reply_seat_id: SeatId | null;
  reply_by: string | null;
  reply_at: string | null;
  shown: { seat_id: SeatId; ts: string }[];
  /** Production waved it away: it fades everywhere. */
  dismissed_at: string | null;
  /** Seats whose screen hid it (the ✕ on the hosts screen). */
  dismissed_by: { seat_id: SeatId; ts: string }[];
  /** Production has seen this host's message (the unread badge). */
  read_at: string | null;
}

export interface CardView {
  id: string;
  module: string;
  title: string;
  priority: number;
  state_path: string | null;
  component: string;
  options: Record<string, unknown>;
  placeholder: boolean;
  pinned: string | null;
  visible: boolean;
}

export interface HostsState {
  seats: Seat[];
  screens: number;
  messages: HostsMessage[];
  layout: CardView[];
  pushed_card: { id: string; by: string; at: string } | null;
  config: {
    canned_messages: string[];
    quick_replies: string[];
    reactions: string[];
    message_expires_s: number;
    chime: boolean;
    chat_size: ChatSize;
    host_phrases: string[];
    production_chime: boolean;
  };
}

/** The remaining seconds at which the now-playing countdown turns amber (brief §3). */
export const COUNTDOWN_AMBER_AT_SECONDS = 10;

/** After an answer, a grown message shrinks back to one line after this long (WP9b §1). */
export const COLLAPSE_AFTER_MS = 4000;

/** Where a hosts screen remembers which seats it represents. */
export const SEATS_STORAGE_KEY = "baja-studio.hosts-seats";

/** A message has been answered once a seat reacted or replied. */
export function isAnswered(message: HostsMessage): boolean {
  return message.reactions.length > 0 || message.reply_text !== null;
}

/** Past its expiry time (never, for a message without one). */
export function isExpired(message: HostsMessage, nowMs: number): boolean {
  return message.expires_at !== null && Date.parse(message.expires_at) <= nowMs;
}

/** Expired, answered or dismissed messages fade on both pages. */
export function isFaded(message: HostsMessage, nowMs: number): boolean {
  return message.dismissed_at !== null || isAnswered(message) || isExpired(message, nowMs);
}

/** An urgent message pulses until someone reacts to it (or it is waved away). */
export function isPulsing(message: HostsMessage): boolean {
  return message.kind === "urgent" && !isAnswered(message) && message.dismissed_at === null;
}

/**
 * How big a message is on a screen that represents `seats`: "large" when it
 * is for one of them (or for all), "small" when it is for a colleague, so
 * they know production is talking to the person next to them.
 */
export function messageSize(message: HostsMessage, seats: SeatId[]): "large" | "small" {
  return addressedSeats(message, seats).length > 0 ? "large" : "small";
}

/** The seats on this screen a message is for (all of them for "all"; none for production's). */
export function addressedSeats(message: HostsMessage, seats: SeatId[]): SeatId[] {
  if (message.to === "all") return seats;
  return seats.filter((seat) => seat === message.to);
}

/** The seat that sent a host's message ("seat:2" → 2), or null for production's. */
export function senderSeat(message: HostsMessage): SeatId | null {
  const match = /^seat:([1-4])$/.exec(message.from);
  return match ? (Number(match[1]) as SeatId) : null;
}

/** A host's message to production. */
export function isFromHost(message: HostsMessage): boolean {
  return message.to === "production";
}

/** Whether one of this screen's seats hid the message with its ✕ (it stays hidden). */
export function isHiddenOn(message: HostsMessage, seats: SeatId[]): boolean {
  return message.dismissed_by.some((entry) => seats.includes(entry.seat_id));
}

/** When the message was first answered (a reaction or the reply), or null. */
export function answeredAtMs(message: HostsMessage): number | null {
  const times = [
    ...message.reactions.map((reaction) => Date.parse(reaction.ts)),
    ...(message.reply_at ? [Date.parse(message.reply_at)] : []),
  ].filter((time) => !Number.isNaN(time));
  if (times.length === 0) return isAnswered(message) ? 0 : null;
  return Math.min(...times);
}

/**
 * Whether a message is drawn grown on this screen, not as a compact line
 * (WP9b §1). A message for this screen grows while it waits for an answer
 * (an urgent one pulses meanwhile), and shrinks back `COLLAPSE_AFTER_MS`
 * after the first answer, when it expires or when production waves it
 * away. A host's own message grows for a moment when production answers it.
 */
export function isExpanded(message: HostsMessage, seats: SeatId[], nowMs: number): boolean {
  if (message.dismissed_at !== null || isExpired(message, nowMs)) return false;
  const answered = answeredAtMs(message);
  if (isFromHost(message)) {
    const sender = senderSeat(message);
    if (sender === null || !seats.includes(sender) || answered === null) return false;
    return nowMs < answered + COLLAPSE_AFTER_MS;
  }
  if (addressedSeats(message, seats).length === 0) return false;
  return answered === null || nowMs < answered + COLLAPSE_AFTER_MS;
}

/** Hosts' messages production has not seen yet, oldest first. */
export function unreadFromHosts(messages: HostsMessage[]): HostsMessage[] {
  return messages.filter((message) => isFromHost(message) && message.read_at === null);
}

/** "Ana", "everyone", "production", or "seat 3" when the seat is unknown. */
export function targetName(target: MessageTarget, seats: Seat[]): string {
  if (target === "all") return "everyone";
  if (target === "production") return "production";
  return seats.find((seat) => seat.id === target)?.screen_name || `seat ${target}`;
}

/** The name of seat `seatId` ("seat 3" when unknown or unnamed). */
export function seatName(seatId: SeatId | null, seats: Seat[]): string {
  return seats.find((seat) => seat.id === seatId)?.screen_name || `seat ${seatId}`;
}

/** The colour messages to this target are drawn in (a neutral one for everyone). */
export function targetColor(target: MessageTarget, seats: Seat[]): string {
  if (target === "all" || target === "production") return "#d8dce2";
  return seats.find((seat) => seat.id === target)?.color ?? "#d8dce2";
}

/** A message's colour: its sender seat's for a host's message, else its target's. */
export function messageColor(message: HostsMessage, seats: Seat[]): string {
  const sender = senderSeat(message);
  return sender !== null ? targetColor(sender, seats) : targetColor(message.to, seats);
}

/** "production:ab12" → "ab12", "seat:2" → "Bruno": who sent it, as a short tag. */
export function senderTag(from: string, seats: Seat[] = []): string {
  if (from.startsWith("production:")) return from.slice("production:".length);
  const seat = /^seat:([1-4])$/.exec(from);
  return seat ? seatName(Number(seat[1]) as SeatId, seats) : from;
}

/** The cards a hosts screen shows: visible ones in layout order, the pushed one first, pinned last. */
export function cardsToShow(state: HostsState): { grid: CardView[]; pinned: CardView[] } {
  const pushedId = state.pushed_card?.id ?? null;
  const floating = state.layout.filter((card) => card.pinned === null);
  const pushed = floating.filter((card) => card.id === pushedId);
  const rest = floating.filter((card) => card.id !== pushedId && card.visible);
  return {
    grid: [...pushed, ...rest],
    pinned: state.layout.filter((card) => card.pinned !== null),
  };
}

/** The value at a dotted state path ("playout.hosts_view") in the whole tree. */
export function valueAt(tree: Record<string, unknown>, path: string | null): unknown {
  if (path === null) return undefined;
  let node: unknown = tree;
  for (const key of path.split(".")) {
    if (typeof node !== "object" || node === null) return undefined;
    node = (node as Record<string, unknown>)[key];
  }
  return node;
}

/** `h:mm:ss` since the stream started, or null without a marker. */
export function streamTime(markerIso: string | null, nowMs: number): string | null {
  if (markerIso === null) return null;
  const total = Math.max(0, Math.floor((nowMs - Date.parse(markerIso)) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

/** The seats this screen represents, from localStorage (none when unset or garbled). */
export function loadScreenSeats(storage: Pick<Storage, "getItem">): SeatId[] | null {
  const saved = storage.getItem(SEATS_STORAGE_KEY);
  if (saved === null) return null;
  try {
    const parsed: unknown = JSON.parse(saved);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((seat): seat is SeatId => [1, 2, 3, 4].includes(seat as number));
  } catch {
    return null;
  }
}

/** Remember which seats this screen represents. */
export function saveScreenSeats(storage: Pick<Storage, "setItem">, seats: SeatId[]): void {
  storage.setItem(SEATS_STORAGE_KEY, JSON.stringify(seats));
}

/**
 * The `set_card_layout` argument after moving one card to `toIndex` among the
 * floating cards (pinned ones are not production's to place).
 */
export function movedLayout(
  layout: CardView[],
  cardId: string,
  toIndex: number,
): { id: string; visible: boolean }[] {
  const floating = layout.filter((card) => card.pinned === null);
  const from = floating.findIndex((card) => card.id === cardId);
  if (from === -1) return layoutChoices(floating);
  const reordered = [...floating];
  const [moved] = reordered.splice(from, 1);
  reordered.splice(Math.max(0, Math.min(toIndex, reordered.length)), 0, moved!);
  return layoutChoices(reordered);
}

/** The `set_card_layout` argument with one card shown or hidden. */
export function toggledLayout(
  layout: CardView[],
  cardId: string,
  visible: boolean,
): { id: string; visible: boolean }[] {
  return layoutChoices(
    layout
      .filter((card) => card.pinned === null)
      .map((card) => (card.id === cardId ? { ...card, visible } : card)),
  );
}

function layoutChoices(cards: CardView[]): { id: string; visible: boolean }[] {
  return cards.map((card) => ({ id: card.id, visible: card.visible }));
}
