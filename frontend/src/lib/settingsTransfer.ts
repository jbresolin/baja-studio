/**
 * Settings export and import (server: modules/home/settings_transfer.py).
 *
 * The file goes to the server by HTTP (`POST /api/home/settings-import`), and
 * the dry run and apply are home-module commands naming the upload, so the
 * command log never holds the file.
 */

import { ApiError } from "./api";

export interface KeyChange {
  key: string;
  old: unknown;
  new: unknown;
  secret: boolean;
}

export interface SectionPlan {
  section: string;
  status: "changes" | "unchanged" | "invalid" | "unknown";
  messages: string[];
  file_settings_version: number | null;
  current_settings_version: number | null;
  added: KeyChange[];
  removed: KeyChange[];
  changed: KeyChange[];
  secrets_to_enter: string[];
}

export interface DryRun {
  token: string;
  app_version: string;
  exported_at: string;
  hostname: string;
  sections: SectionPlan[];
  needs_partial: boolean;
  has_changes: boolean;
}

export interface ApplyResult {
  results: { section: string; result: string }[];
  secrets_to_enter: string[];
}

export interface ExportResult {
  file_name: string;
  document: unknown;
}

/**
 * Audio's link matrix as designed and the seats' offsets (WP9b §4). They
 * live in audio's tables, not in settings, so the page adds them to the
 * file it saves under `audio_routing` and sends them back to audio on
 * import (server: modules/audio/routing_commands.py).
 */
export interface AudioRouting {
  links: Record<string, string[]>;
  offsets: Record<string, number>;
}

export interface AudioRoutingPlan {
  added: string[];
  removed: string[];
  offsets_changed: string[];
  applied: boolean;
}

/** The key the page writes audio's routing under in the settings file. */
export const AUDIO_ROUTING_KEY = "audio_routing";

/** The audio routing a chosen settings file carries, or null (older file, or audio was off). */
export async function readAudioRouting(file: Blob): Promise<AudioRouting | null> {
  try {
    const document = JSON.parse(await file.text()) as Record<string, unknown> | null;
    const routing = document?.[AUDIO_ROUTING_KEY];
    return typeof routing === "object" && routing !== null ? (routing as AudioRouting) : null;
  } catch {
    // Not JSON: the server's dry run says so in its own words.
    return null;
  }
}

/** How many things an audio routing import changes. */
export function routingChangeCount(plan: AudioRoutingPlan): number {
  return plan.added.length + plan.removed.length + plan.offsets_changed.length;
}

/** Send a chosen file to the server; resolves with the id the commands name it by. */
export async function uploadSettingsFile(file: Blob): Promise<string> {
  const response = await fetch("/api/home/settings-import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: file,
  });
  const body = (await response.json().catch(() => null)) as {
    upload_id?: string;
    detail?: unknown;
  } | null;
  if (!response.ok || !body?.upload_id) {
    throw new ApiError(response.status, body?.detail ?? "the upload failed");
  }
  return body.upload_id;
}

/** Offer `document` to the browser as a JSON file called `fileName`. */
export function saveJsonFile(fileName: string, document: unknown): void {
  const blob = new Blob([JSON.stringify(document, null, 2) + "\n"], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = window.document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

/** A value as the diff shows it: strings quoted, nothing shown as "—". */
export function shownValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value);
}
