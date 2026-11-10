/**
 * One settings section edited as JSON: what the generic Settings page shows
 * for a module that has its own settings page (WP5c §1), so its fields are
 * never offered twice. Text that is not valid JSON is refused out loud,
 * nothing is saved, and the server's per-field refusals are listed.
 * Saving sends the version the edit started from, like every settings save.
 */

import { useEffect, useState } from "react";

import {
  getSettings,
  saveSettings,
  SettingsConflictError,
  SettingsValidationError,
  type SettingsDocument,
} from "../lib/api";

export function SettingsJsonEditor({ section }: { section: string }) {
  const [saved, setSaved] = useState<SettingsDocument | null>(null);
  const [text, setText] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [loadCount, setLoadCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSettings(section)
      .then((document) => {
        if (cancelled) return;
        setSaved(document);
        setText(JSON.stringify(document.values, null, 2));
        setProblems([]);
      })
      .catch((error: Error) => {
        if (!cancelled) setProblems([error.message]);
      });
    return () => {
      cancelled = true;
    };
  }, [section, loadCount]);

  if (!saved) {
    return problems.length > 0 ? (
      <div className="notice notice-error">{problems[0]}</div>
    ) : (
      <p className="muted">Loading…</p>
    );
  }

  async function save() {
    if (!saved) return;
    setNotice(null);
    let values: Record<string, unknown>;
    try {
      values = JSON.parse(text) as Record<string, unknown>;
    } catch (error) {
      setProblems([`Not valid JSON, nothing saved: ${(error as Error).message}`]);
      return;
    }
    try {
      const document = await saveSettings(section, saved.version, values);
      setSaved(document);
      setText(JSON.stringify(document.values, null, 2));
      setProblems([]);
      setNotice("Saved.");
    } catch (error) {
      if (error instanceof SettingsValidationError) {
        setProblems(error.fieldErrors.map((problem) => `${problem.field}: ${problem.message}`));
      } else if (error instanceof SettingsConflictError) {
        setProblems([
          "Someone else saved these settings meanwhile: load the latest and redo the change.",
        ]);
      } else {
        setProblems([(error as Error).message]);
      }
    }
  }

  return (
    <div className="advanced-json">
      <textarea
        aria-label={`${section} settings JSON`}
        rows={24}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="button-row">
        <button type="button" onClick={() => void save()}>
          Save JSON
        </button>
        <button type="button" onClick={() => setLoadCount((count) => count + 1)}>
          Load latest
        </button>
        <span className="muted">version {saved.version}</span>
      </div>
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      {problems.length > 0 && (
        <div className="notice notice-error" role="alert" data-testid="json-errors">
          <ul>
            {problems.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
