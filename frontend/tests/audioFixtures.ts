/**
 * Audio settings and live state for the page tests, shaped like the server's.
 * Small on purpose: two visible channels, two macros, one scene.
 */

import {
  AUX_BUSES,
  CHANNEL_IDS,
  type AudioSettingsValues,
  type AudioState,
  type ButtonAction,
} from "../src/lib/audio";
import type { SeatAudioView } from "../src/lib/seatAudio";

export function audioSettings(): AudioSettingsValues {
  const channels = Object.fromEntries(
    CHANNEL_IDS.map((channel) => [channel, { label: "", color: "#5b7fff", visible: false }]),
  );
  channels["1"] = { label: "HOST 1", color: "#e74c3c", visible: true };
  channels["5"] = { label: "PROD 1", color: "#3498db", visible: true };
  channels["bus1"] = { label: "IEM HOST 1", color: "#e74c3c", visible: false };
  const buttons: Record<string, ButtonAction> = {};
  for (let strip = 1; strip <= 8; strip += 1) {
    buttons[`${strip}_mute`] = { type: "mute", channel: null, bus: null, macro: null };
  }
  return {
    enabled: true,
    mixer: { host: "192.168.1.10", port: 10024, enabled: true },
    channels,
    midi: {
      enabled: true,
      device_name_match: "SMC",
      faders: { "1": "1", "2": "5" },
      buttons,
      knobs: { "1": { target: "aux_send", bus: null } },
      row_buses: { solo: "main", rec: "bus5", sel: "bus1" },
      knob_step_db: 1.5,
      fader_takeover: "pickup",
      send_fader_feedback: true,
    },
    macros: [
      {
        id: "host1-talk-prod",
        label: "Host 1 → Prod",
        color: "#e74c3c",
        mode: "toggle",
        on_steps: [{ op: "store_levels", points: [{ channel: "1", bus: "main" }], silence: true }],
        off_steps: [{ op: "restore_levels", points: [{ channel: "1", bus: "main" }] }],
      },
      {
        id: "prod1-talk-hosts",
        label: "Prod 1 → Hosts",
        color: "#3498db",
        mode: "toggle",
        on_steps: [{ op: "restore_levels", points: [{ channel: "5", bus: "bus1" }] }],
        off_steps: [{ op: "store_levels", points: [{ channel: "5", bus: "bus1" }], silence: true }],
      },
    ],
    scenes: [{ index: 1, label: "Race", snapshot: 3, macro: null }],
    d200: { enabled: true, state_poll_hint_s: 1 },
    talkback_bus: "bus6",
    production_bus: "bus5",
    production_channels: ["5"],
    seats: [
      { seat_id: 1, channel: "1", iem_bus: "bus1" },
      { seat_id: 2, channel: null, iem_bus: "bus2" },
      { seat_id: 3, channel: null, iem_bus: "bus3" },
      { seat_id: 4, channel: null, iem_bus: "bus4" },
    ],
    hosts_can_adjust_mix: true,
    personal_mix_step_db: 1,
  };
}

/** `audio.hosts_view` with seat 1 live and a two-source personal mix; tests change the rest. */
export function seatAudioView(overrides: Partial<SeatAudioView> = {}): SeatAudioView {
  const seat = (seatId: number, state: SeatAudioView["seats"][string]["state"]) => ({
    seat_id: seatId,
    channel: seatId === 1 ? "1" : null,
    channel_label: seatId === 1 ? "HOST 1" : null,
    iem_bus: `bus${seatId}`,
    iem_label: `IEM ${seatId}`,
    state,
    production_to_you: false,
  });
  return {
    mixer_online: true,
    can_adjust: true,
    step_db: 1,
    talkback: { active: false, label: "CAMERA OP" },
    seats: {
      "1": seat(1, "live"),
      "2": seat(2, "not_mapped"),
      "3": seat(3, "not_mapped"),
      "4": seat(4, "not_mapped"),
    },
    mixes: {
      "1": [
        {
          channel: "9",
          label: "SHOTGUN",
          link: "linked",
          main_level: 0.75,
          main_db: "0.0 dB",
          offset_db: 2,
          send_level: 0.8,
          send_db: "2.0 dB",
        },
        {
          channel: "aux",
          label: "PC AUDIO",
          link: "unlinked",
          main_level: 0.75,
          main_db: "0.0 dB",
          offset_db: null,
          send_level: 0.5,
          send_db: "-10.0 dB",
        },
      ],
    },
    pad_change: null,
    ...overrides,
  };
}

export function audioState(overrides: Partial<AudioState> = {}): AudioState {
  const sends = Object.fromEntries(AUX_BUSES.map((bus) => [bus, 0.5]));
  return {
    mixer: {
      connection: "ok",
      host: "192.168.1.10",
      message: "",
      name: "XR18-5E",
      model: "XR18",
      firmware: "1.22",
      packets_per_s: { sent: 1, received: 1 },
    },
    channels: {
      "1": { fader: 0.75, db: "0.0 dB", muted: false, name: "Host1", pan: 0.5, sends },
      "5": { fader: 0.5, db: "-10.0 dB", muted: true, name: "", pan: 0.5, sends },
    },
    links: { "1": { bus1: "linked" }, "5": { bus1: "paused" } },
    stored_levels: {},
    offsets: {},
    hosts_view: seatAudioView(),
    macros: { "host1-talk-prod": { active: false }, "prod1-talk-hosts": { active: true } },
    midi: {
      status: "ok",
      device: "SMC-Mixer",
      message: "",
      selected_bus: null,
      learn: { armed: false, by: null, captured: null },
      pickup: {},
    },
    last_source: { "1:main": "device:mvave" },
    d200: { last_seen: null },
    settings_version: 4,
    ...overrides,
  };
}
