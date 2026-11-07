/**
 * Generic settings editor: works for any module because the form is built
 * from the JSON Schema the server publishes for that module's settings.
 *
 * Saving sends the version the edit started from; if someone else saved in
 * the meantime the server refuses (409) and we say so instead of overwriting.
 */

import { useEffect, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";

import { SchemaForm } from "../components/SchemaForm";
import {
  getSettings,
  getSettingsSchema,
  listSettingsSections,
  saveSettings,
  SettingsConflictError,
  SettingsValidationError,
  type SettingsDocument,
  type SettingsSchema,
  type SettingsSection,
  type SettingsValues,
} from "../lib/api";
import { SettingsTransfer } from "./SettingsTransfer";

// The export/import page lives beside the sections, under a name no module uses.
const TRANSFER_SECTION = "transfer";

type Notice = { kind: "saved" | "error" | "conflict"; text: string } | null;

export function Settings() {
  const { section } = useParams();
  const navigate = useNavigate();
  const [sections, setSections] = useState<SettingsSection[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    listSettingsSections()
      .then(setSections)
      .catch((error: Error) => setLoadError(error.message));
  }, []);

  // With no section in the URL, open the first one.
  useEffect(() => {
    if (!section && sections.length > 0)
      navigate(`/settings/${sections[0]!.section}`, { replace: true });
  }, [section, sections, navigate]);

  return (
    <div className="settings-layout">
      <nav className="settings-sections">
        {sections.map((item) => (
          <NavLink key={item.section} to={`/settings/${item.section}`}>
            {item.title}
          </NavLink>
        ))}
        <NavLink to={`/settings/${TRANSFER_SECTION}`}>Export / import</NavLink>
      </nav>
      <div className="settings-editor">
        {loadError && <div className="notice notice-error">{loadError}</div>}
        {section === TRANSFER_SECTION ? (
          <SettingsTransfer />
        ) : (
          section && <SectionEditor key={section} section={section} />
        )}
      </div>
    </div>
  );
}

/** Loads, edits and saves one section. Re-mounted (via `key`) when the section changes. */
export function SectionEditor({ section }: { section: string }) {
  const [schema, setSchema] = useState<SettingsSchema | null>(null);
  const [saved, setSaved] = useState<SettingsDocument | null>(null);
  const [draft, setDraft] = useState<SettingsValues>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<Notice>(null);
  const [saving, setSaving] = useState(false);

  // Bumping this number reloads the section from the server ("Load latest").
  const [loadCount, setLoadCount] = useState(0);

  useEffect(() => {
    // If the section changes or the page closes before the answers arrive,
    // `cancelled` stops us from writing stale results into the form.
    let cancelled = false;
    Promise.all([getSettingsSchema(section), getSettings(section)])
      .then(([loadedSchema, document]) => {
        if (cancelled) return;
        setSchema(loadedSchema);
        setSaved(document);
        setDraft(document.values);
        setFieldErrors({});
      })
      .catch((error: Error) => {
        if (!cancelled) setNotice({ kind: "error", text: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, [section, loadCount]);

  if (!schema || !saved) {
    return notice ? (
      <div className="notice notice-error">{notice.text}</div>
    ) : (
      <p className="muted">Loading…</p>
    );
  }

  const isDirty = JSON.stringify(draft) !== JSON.stringify(saved.values);

  async function save() {
    if (!saved) return;
    setSaving(true);
    setNotice(null);
    try {
      const document = await saveSettings(section, saved.version, draft);
      setSaved(document);
      setDraft(document.values);
      setFieldErrors({});
      setNotice({ kind: "saved", text: "Saved." });
    } catch (error) {
      if (error instanceof SettingsConflictError) {
        setNotice({
          kind: "conflict",
          text:
            "Someone else changed these settings while you were editing " +
            `(now version ${error.currentVersion}). Load the latest values and redo your change.`,
        });
      } else if (error instanceof SettingsValidationError) {
        setFieldErrors(Object.fromEntries(error.fieldErrors.map((e) => [e.field, e.message])));
        setNotice({ kind: "error", text: "Some values were not accepted; see the fields below." });
      } else {
        setNotice({ kind: "error", text: (error as Error).message });
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      // The server validates and explains problems per field; native browser
      // checks would block the save with less helpful, inconsistent messages.
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <SchemaForm
        schema={schema}
        values={draft}
        fieldErrors={fieldErrors}
        onChange={(field, value) => setDraft((current) => ({ ...current, [field]: value }))}
      />
      <div className="button-row">
        <button type="submit" disabled={!isDirty || saving}>
          Save
        </button>
        <button
          type="button"
          disabled={!isDirty || saving}
          onClick={() => {
            setDraft(saved.values);
            setFieldErrors({});
            setNotice(null);
          }}
        >
          Discard
        </button>
        <span className="muted">version {saved.version}</span>
      </div>
      {notice && (
        <div className={notice.kind === "saved" ? "notice" : "notice notice-error"} role="status">
          {notice.text}
          {notice.kind === "conflict" && (
            <button
              type="button"
              onClick={() => {
                setNotice(null);
                setLoadCount((count) => count + 1);
              }}
            >
              Load latest
            </button>
          )}
        </div>
      )}
    </form>
  );
}
