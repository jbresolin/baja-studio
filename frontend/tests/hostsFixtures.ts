/** Sample hosts data shared by the hosts tests. */

import type { CardView, HostsMessage, HostsState, Seat } from "../src/lib/hosts";

/** Ana on seat 1 (red), Bruno on seat 2 (orange), guests off; nobody online. */
export function seats(): Seat[] {
  return [
    seat({ id: 1, screen_name: "Ana", color: "#e74c3c" }),
    seat({ id: 2, screen_name: "Bruno", color: "#e67e22" }),
    seat({ id: 3, screen_name: "Guest 1", color: "#f1c40f", enabled: false }),
    seat({ id: 4, screen_name: "Guest 2", color: "#2ecc71", enabled: false }),
  ];
}

export function seat(overrides: Partial<Seat> & Pick<Seat, "id">): Seat {
  return {
    screen_name: `Seat ${overrides.id}`,
    color: "#5b7fff",
    enabled: true,
    online: false,
    screens: 0,
    ...overrides,
  };
}

/** A card as the server lays it out; tests change what they care about. */
export function card(overrides: Partial<CardView> & Pick<CardView, "id">): CardView {
  return {
    module: "playout",
    title: overrides.id,
    priority: 50,
    state_path: null,
    component: overrides.id,
    options: {},
    placeholder: false,
    pinned: null,
    visible: true,
    ...overrides,
  };
}

/** The default layout: playout's two cards, audio, the clock, a placeholder, messages. */
export function layout(): CardView[] {
  return [
    card({ id: "now_playing", title: "Now playing", state_path: "playout.hosts_view" }),
    card({ id: "next_up", title: "Next up", state_path: "playout.hosts_view" }),
    card({
      id: "audio",
      module: "audio",
      title: "Audio",
      state_path: "audio.hosts_view",
      component: "seat_audio",
    }),
    card({
      id: "clock",
      module: "hosts",
      title: "Clock",
      state_path: "playout.export",
      component: "clock",
    }),
    card({
      id: "standings",
      module: "hosts",
      title: "Standings",
      component: "placeholder",
      placeholder: true,
      visible: false,
    }),
    card({
      id: "messages",
      module: "hosts",
      title: "Messages",
      state_path: "hosts.messages",
      component: "messages",
      pinned: "bottom",
    }),
  ];
}

/** A message from production page "prod1", sent at 12:00:00 UTC, nothing happened yet. */
export function message(overrides: Partial<HostsMessage> & Pick<HostsMessage, "id">): HostsMessage {
  return {
    ts: "2026-10-01T12:00:00+00:00",
    from: "production:prod1",
    to: "all",
    text: "hello",
    kind: "info",
    expires_s: null,
    expires_at: null,
    reactions: [],
    reply_text: null,
    reply_seat_id: null,
    reply_by: null,
    reply_at: null,
    shown: [],
    dismissed_at: null,
    dismissed_by: [],
    read_at: null,
    ...overrides,
  };
}

/** A host's message to production ("seat:<id>" → "production"), unread. */
export function hostMessage(
  overrides: Partial<HostsMessage> & Pick<HostsMessage, "id"> & { seat: number },
): HostsMessage {
  const { seat: seatId, ...rest } = overrides;
  return message({ from: `seat:${seatId}`, to: "production", text: "pronto", ...rest });
}

export function hostsState(overrides: Partial<HostsState> = {}): HostsState {
  return {
    seats: seats(),
    screens: 0,
    messages: [],
    layout: layout(),
    pushed_card: null,
    config: {
      canned_messages: ["30 s", "wrap up"],
      quick_replies: ["ok", "need 1 min"],
      reactions: ["👍", "👎", "✋", "❓"],
      message_expires_s: 60,
      chime: false,
      chat_size: "small",
      host_phrases: ["preciso de 1 min", "pronto"],
      production_chime: true,
    },
    ...overrides,
  };
}
