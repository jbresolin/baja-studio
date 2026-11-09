/** Sample playout data shared by the playout tests. */

import type { PlayoutClip, PlayoutState } from "../src/lib/playout";

/**
 * A present, unplayed manual clip; tests change what they care about. Kind
 * and group follow from the rest as the server works them out: an
 * orchestrator clip is "pipeline" in its prova's group, a manual one is in
 * its folder's group.
 */
export function clip(overrides: Partial<PlayoutClip> = {}): PlayoutClip {
  const merged: PlayoutClip = {
    id: 1,
    source: "manual",
    kind: overrides.source === "orchestrator" ? "pipeline" : "manual",
    group: "",
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
  if (overrides.group !== undefined) return merged;
  const group =
    merged.kind === "pipeline"
      ? (merged.prova ?? "Unmatched")
      : merged.kind === "unmatched"
        ? "Unmatched"
        : merged.folder_label;
  return { ...merged, group };
}

/** Playout state with OBS connected, an empty queue and nothing on air. */
export function playoutState(overrides: Partial<PlayoutState> = {}): PlayoutState {
  return {
    obs: {
      connection: "connected",
      message: "",
      current_scene: "Main",
      scenes: ["Main", "Interview"],
    },
    program: null,
    preview: null,
    queue: [],
    queue_total_s: 0,
    queue_unknown: 0,
    library: {},
    library_summary: { total: 0, missing: 0, unplayed: 0, unmatched: 0, per_group: {} },
    playlists: [],
    return_target: {
      mode: "previous",
      chosen_scene: null,
      previous_scene: "Main",
      scene: "Main",
      warning: null,
    },
    tools: { ffprobe: "/usr/bin/ffprobe", ffmpeg: "/usr/bin/ffmpeg", problem: null },
    cue_problem: null,
    notice: null,
    hosts_view: { on_air: null, next: [], queue_remaining_s: 0 },
    sync_status: { state: "disabled", last_success_at: null, error: null },
    folders: { errors: {} },
    auto_play_next: false,
    take_confirmation: null,
    export: { stream_start_marker: null },
    ...overrides,
  };
}
