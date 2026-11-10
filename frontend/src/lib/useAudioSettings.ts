/**
 * The audio setup page's settings: a local draft with explicit Save and Discard.
 *
 * The 2026 page lost macro edits because every state broadcast re-rendered
 * it from the server. Here the draft is the page's own copy: nothing from
 * the WebSocket ever writes into it. It is replaced only when the operator
 * saves, discards, or loads the latest after a conflict.
 *
 * Saving sends the version the draft started from (WP0 rules): if someone
 * saved in between, the server refuses (409) and nothing is overwritten.
 * `audio.settings_version` in the live state tells the page someone saved:
 * with no unsaved edits it loads the new version by itself; with unsaved
 * edits it only says so.
 */

import { useCallback, useEffect, useState } from "react";

import {
  getSettings,
  saveSettings,
  SettingsConflictError,
  SettingsValidationError,
  type SettingsDocument,
} from "./api";
import { differs, type AudioSettingsValues } from "./audio";

/** A deep copy (settings are plain JSON). */
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export type AudioSaveNotice = { kind: "saved" | "error" | "conflict"; text: string } | null;

export interface AudioSettingsHandle {
  /** What was last loaded or saved (null while loading). */
  saved: AudioSettingsValues | null;
  /** The operator's working copy. */
  draft: AudioSettingsValues | null;
  version: number | null;
  dirty: boolean;
  saving: boolean;
  notice: AudioSaveNotice;
  /** A newer version saved elsewhere while this page has unsaved edits. */
  newerVersion: number | null;
  edit: (change: (draft: AudioSettingsValues) => AudioSettingsValues) => void;
  /** The saved version, or null when nothing was saved (a conflict or an error). */
  save: () => Promise<number | null>;
  discard: () => void;
  loadLatest: () => void;
}

export function useAudioSettings(liveVersion: number | undefined): AudioSettingsHandle {
  const [document, setDocument] = useState<SettingsDocument | null>(null);
  const [draft, setDraft] = useState<AudioSettingsValues | null>(null);
  const [notice, setNotice] = useState<AudioSaveNotice>(null);
  const [saving, setSaving] = useState(false);
  const [loadCount, setLoadCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSettings("audio")
      .then((loaded) => {
        if (cancelled) return;
        setDocument(loaded);
        setDraft(clone(loaded.values) as unknown as AudioSettingsValues);
      })
      .catch((error: Error) => {
        if (!cancelled) setNotice({ kind: "error", text: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, [loadCount]);

  const saved = (document?.values as unknown as AudioSettingsValues | undefined) ?? null;
  const dirty = saved !== null && draft !== null && differs(saved, draft);
  const newer =
    document !== null && liveVersion !== undefined && liveVersion > document.version
      ? liveVersion
      : null;

  // Someone saved elsewhere and we have nothing unsaved: take theirs.
  useEffect(() => {
    if (newer === null || dirty || saving) return;
    let cancelled = false;
    getSettings("audio")
      .then((loaded) => {
        if (cancelled) return;
        setDocument(loaded);
        setDraft(clone(loaded.values) as unknown as AudioSettingsValues);
      })
      .catch(() => undefined); // the next version change tries again
    return () => {
      cancelled = true;
    };
  }, [newer, dirty, saving]);

  const save = useCallback(async (): Promise<number | null> => {
    if (!document || !draft) return null;
    setSaving(true);
    setNotice(null);
    try {
      const result = await saveSettings(
        "audio",
        document.version,
        draft as unknown as Record<string, unknown>,
      );
      setDocument(result);
      setDraft(clone(result.values) as unknown as AudioSettingsValues);
      setNotice({ kind: "saved", text: "Saved and applied." });
      return result.version;
    } catch (error) {
      if (error instanceof SettingsConflictError) {
        setNotice({
          kind: "conflict",
          text:
            `Someone else saved the audio setup (now version ${error.currentVersion}). ` +
            "Your edits are still here; load the latest to see theirs (yours are dropped).",
        });
      } else if (error instanceof SettingsValidationError) {
        setNotice({ kind: "error", text: `Not saved: ${error.message}` });
      } else {
        setNotice({ kind: "error", text: (error as Error).message });
      }
      return null;
    } finally {
      setSaving(false);
    }
  }, [document, draft]);

  return {
    saved,
    draft,
    version: document?.version ?? null,
    dirty,
    saving,
    notice,
    newerVersion: dirty ? newer : null,
    edit: (change) => setDraft((current) => (current ? change(clone(current)) : current)),
    save,
    discard: () => {
      setNotice(null);
      if (saved) setDraft(clone(saved));
    },
    loadLatest: () => {
      setNotice(null);
      setLoadCount((count) => count + 1);
    },
  };
}
