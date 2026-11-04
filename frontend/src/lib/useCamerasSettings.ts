/**
 * The cameras module's settings for the configuration page: load, save, and
 * the WP0 rules for saving (the version the edit started from is sent; a
 * newer one on the server is a conflict, never a silent overwrite).
 *
 * Saves send only the part edited (`{cameras}`, `{controllers}` or
 * `{motion}`); the server merges it with the rest.
 */

import { useCallback, useEffect, useState } from "react";

import {
  getSettings,
  saveSettings,
  SettingsConflictError,
  SettingsValidationError,
  type SettingsDocument,
} from "./api";
import type { CamerasSettingsValues } from "./cameras";

export type SaveNotice = { kind: "saved" | "error" | "conflict"; text: string } | null;

export interface CamerasSettingsHandle {
  values: CamerasSettingsValues | null;
  version: number | null;
  /** Field problems from the last refused save, keyed like "cameras.0.address.host". */
  fieldErrors: Record<string, string>;
  notice: SaveNotice;
  saving: boolean;
  /** Save part of the settings; resolves true when saved. */
  save: (patch: Partial<CamerasSettingsValues>) => Promise<boolean>;
  /** Read the settings again (after a conflict, or a change made by a command). */
  reload: () => void;
  clearNotice: () => void;
}

export function useCamerasSettings(): CamerasSettingsHandle {
  const [document, setDocument] = useState<SettingsDocument | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<SaveNotice>(null);
  const [saving, setSaving] = useState(false);
  const [loadCount, setLoadCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSettings("cameras")
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

  const save = useCallback(
    async (patch: Partial<CamerasSettingsValues>): Promise<boolean> => {
      if (!document) return false;
      setSaving(true);
      setNotice(null);
      try {
        const saved = await saveSettings(
          "cameras",
          document.version,
          patch as Record<string, unknown>,
        );
        setDocument(saved);
        setFieldErrors({});
        setNotice({ kind: "saved", text: "Saved and applied." });
        return true;
      } catch (error) {
        if (error instanceof SettingsConflictError) {
          setNotice({
            kind: "conflict",
            text:
              "Someone else changed the camera settings while you were editing " +
              `(now version ${error.currentVersion}). Load the latest and redo your change.`,
          });
        } else if (error instanceof SettingsValidationError) {
          setFieldErrors(
            Object.fromEntries(
              error.fieldErrors.map((problem) => [problem.field, problem.message]),
            ),
          );
          setNotice({ kind: "error", text: `Not saved: ${error.message}` });
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
    values: (document?.values as unknown as CamerasSettingsValues | undefined) ?? null,
    version: document?.version ?? null,
    fieldErrors,
    notice,
    saving,
    save,
    reload: () => {
      setNotice(null);
      setFieldErrors({});
      setLoadCount((count) => count + 1);
    },
    clearNotice: () => setNotice(null),
  };
}

/** The field errors under `prefix` ("cameras.2"), with the prefix taken off ("address.host"). */
export function errorsUnder(
  fieldErrors: Record<string, string>,
  prefix: string,
): Record<string, string> {
  const found: Record<string, string> = {};
  for (const [field, message] of Object.entries(fieldErrors)) {
    if (field === prefix) found[""] = message;
    else if (field.startsWith(`${prefix}.`)) found[field.slice(prefix.length + 1)] = message;
  }
  return found;
}
