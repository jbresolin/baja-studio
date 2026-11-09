/**
 * Playout's settings as the playout settings page edits them (WP5b §1, §4).
 *
 * Shapes mirror server/baja_studio/playout/config.py. The folders form's
 * "browse" asks the server for its own folders (`GET /api/playout/browse`),
 * since the page may run on another machine. Audio macros for the on-air
 * actions come from audio's live state (which macros exist) and its settings
 * (their labels).
 */

import { useEffect, useState } from "react";

import { getSettings } from "./api";
import type { OnAirActions } from "./playout";
import { useModuleState } from "./stateHooks";

export interface LocalFolderValues {
  path: string;
  label: string;
  recursive: boolean;
  receiver_root: boolean;
}

export interface PlayoutSettingsValues {
  enabled: boolean;
  sources: {
    orchestrator: Record<string, unknown>;
    local_folders: LocalFolderValues[];
  };
  obs: {
    host: string;
    port: number;
    password: string;
    scene_a: string;
    scene_b: string;
    source_a: string;
    source_b: string;
    post_clip: "return" | "hold" | "fade";
    fade_ms: number;
    cut_transition: string;
    fade_transition: string;
  };
  tools: { ffprobe_path: string; ffmpeg_path: string };
  on_air_actions: OnAirActions;
  [section: string]: unknown;
}

export interface FolderListing {
  path: string;
  parent: string | null;
  folders: { name: string; path: string }[];
  error: string | null;
}

/** Subfolders of `path` on the streaming PC (drives when empty). */
export async function browseFolders(path: string): Promise<FolderListing> {
  const response = await fetch(`/api/playout/browse?path=${encodeURIComponent(path)}`);
  if (!response.ok) throw new Error(`browse failed with status ${response.status}`);
  return (await response.json()) as FolderListing;
}

/** An empty folder row for the form. */
export function newFolder(isFirst: boolean): LocalFolderValues {
  return { path: "", label: "", recursive: true, receiver_root: isFirst };
}

/** The folders with `index` as the only receiver root (the form's radio). */
export function withReceiverRoot(folders: LocalFolderValues[], index: number): LocalFolderValues[] {
  return folders.map((folder, position) => ({ ...folder, receiver_root: position === index }));
}

export interface AudioMacro {
  id: string;
  label: string;
}

/** The audio macros an on-air action can run: ids from audio's state, labels from its settings. */
export function useAudioMacros(): AudioMacro[] {
  const audio = useModuleState<{ macros?: Record<string, unknown> }>("audio");
  const [labels, setLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    getSettings("audio")
      .then((document) => {
        const macros = (document.values.macros ?? []) as { id: string; label: string }[];
        if (!cancelled)
          setLabels(Object.fromEntries(macros.map((macro) => [macro.id, macro.label])));
      })
      .catch(() => {
        // Audio switched off or unreachable: the ids alone still work.
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const ids = new Set([...Object.keys(audio?.macros ?? {}), ...Object.keys(labels)]);
  return [...ids]
    .map((id) => ({ id, label: labels[id] || id }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

/** The macro an action runs, if it is an audio `run_macro` action. */
export function actionMacro(action: {
  module: string;
  command: string;
  args: Record<string, unknown>;
}): string | null {
  if (action.module !== "audio" || action.command !== "run_macro") return null;
  const macro = action.args.macro;
  return typeof macro === "string" ? macro : null;
}
