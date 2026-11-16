/**
 * The feed list as saved in the feeds settings, for the Feeds page: load it,
 * save a changed list, and the WP0 rules for saving (the version the edit
 * started from is sent; a newer one on the server is a conflict, never a
 * silent overwrite).
 *
 * Saves send only `{feeds}`; the server merges it with the ports and the
 * rest. Passwords come back masked and go back masked to stay unchanged.
 */

import { useCallback, useEffect, useState } from "react";

import {
  getSettings,
  saveSettings,
  SettingsConflictError,
  SettingsValidationError,
  type SettingsDocument,
} from "./api";
import type { FeedSettings } from "./feeds";

export type FeedsNotice = { kind: "saved" | "error"; text: string } | null;

export interface FeedsSettingsHandle {
  feeds: FeedSettings[] | null;
  notice: FeedsNotice;
  saving: boolean;
  /** Bumped after every save, so other editors of the section reload. */
  generation: number;
  /** Save the whole feed list; resolves true when the server accepted it. */
  saveFeeds: (feeds: FeedSettings[]) => Promise<boolean>;
  reload: () => void;
}

export function useFeedsSettings(): FeedsSettingsHandle {
  const [document, setDocument] = useState<SettingsDocument | null>(null);
  const [notice, setNotice] = useState<FeedsNotice>(null);
  const [saving, setSaving] = useState(false);
  const [loadCount, setLoadCount] = useState(0);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSettings("feeds")
      .then((loaded) => {
        if (!cancelled) setDocument(loaded);
      })
      .catch((error: Error) => {
        if (!cancelled) setNotice({ kind: "error", text: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, [loadCount]);

  const saveFeeds = useCallback(
    async (feeds: FeedSettings[]): Promise<boolean> => {
      if (!document) return false;
      setSaving(true);
      setNotice(null);
      try {
        const saved = await saveSettings("feeds", document.version, { feeds });
        setDocument(saved);
        setGeneration((count) => count + 1);
        setNotice({ kind: "saved", text: "Saved. MediaMTX takes the change in a few seconds." });
        return true;
      } catch (error) {
        if (error instanceof SettingsConflictError) {
          setNotice({
            kind: "error",
            text: "Someone else changed the feeds meanwhile: the latest list is loaded, redo your change.",
          });
          setLoadCount((count) => count + 1);
        } else if (error instanceof SettingsValidationError) {
          setNotice({
            kind: "error",
            text: error.fieldErrors.map((problem) => problem.message).join("; "),
          });
        } else {
          setNotice({ kind: "error", text: (error as Error).message });
        }
        return false;
      } finally {
        setSaving(false);
      }
    },
    [document],
  );

  return {
    feeds: document ? ((document.values.feeds as FeedSettings[] | undefined) ?? []) : null,
    notice,
    saving,
    generation,
    saveFeeds,
    reload: useCallback(() => setLoadCount((count) => count + 1), []),
  };
}
