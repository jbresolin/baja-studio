/** Sample playout data shared by the playout tests. */

import type { PlayoutClip, PlayoutState } from "../src/lib/playout";

/** A present, unplayed manual clip; tests change what they care about. */
export function clip(overrides: Partial<PlayoutClip> = {}): PlayoutClip {
  return {
    id: 1,
    source: "manual",
    title: "Intro",
    file_path: "/clips/intro.mp4",
    relative_path: "intro.mp4",
    folder_label: "Clips",
    missing: false,
    queueable: true,
    duration_s: 30,
    probe_error: null,
    prova: null,
    car_number: null,
    team: null,
    passagem: null,
    label: null,
    show_pass: true,
    notes: "",
    played_count: 0,
    last_played_at: null,
    has_thumbnail: false,
    delivered_at: null,
    ...overrides,
  };
}

/** Playout state with OBS connected, an empty queue and nothing on air. */
export function playoutState(overrides: Partial<PlayoutState> = {}): PlayoutState {
  return {
    obs: { connection: "connected", message: "", current_scene: "Main" },
    program: null,
    preview: null,
    queue: [],
    queue_total_s: 0,
    library: {},
    library_summary: { total: 0, missing: 0, unplayed: 0, per_prova: {} },
    sync_status: { state: "disabled", last_success_at: null, error: null },
    folders: { errors: {} },
    auto_play_next: false,
    take_confirmation: null,
    export: { stream_start_marker: null },
    ...overrides,
  };
}
