/**
 * Settings → Export / import: every module's settings to one file, and back.
 *
 * Import is two steps: choose a file (the server checks it and shows what
 * would change per module), then Apply. Modules that cannot be imported
 * block the Apply unless "apply partial" is ticked.
 */

import { useState, type DragEvent } from "react";

import {
  saveJsonFile,
  shownValue,
  uploadSettingsFile,
  type ApplyResult,
  type DryRun,
  type ExportResult,
  type KeyChange,
  type SectionPlan,
} from "../lib/settingsTransfer";
import { useSendCommand } from "../lib/stateHooks";

export function SettingsTransfer() {
  return (
    <div className="settings-transfer">
      <h2>Export</h2>
      <p className="muted">
        One file with every module&apos;s settings. Passwords and tokens are left out and listed, so
        the file can be shared; the queue, link states and logs are not settings and stay out.
      </p>
      <ExportButton />
      <h2>Import</h2>
      <ImportPanel />
    </div>
  );
}

function ExportButton() {
  const sendCommand = useSendCommand();
  const [error, setError] = useState<string | null>(null);

  async function exportSettings() {
    setError(null);
    try {
      const result = (await sendCommand("home", "export_settings")) as ExportResult;
      saveJsonFile(result.file_name, result.document);
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  return (
    <div className="button-row">
      <button onClick={() => void exportSettings()}>Export settings</button>
      {error && <span className="notice notice-error">{error}</span>}
    </div>
  );
}

function ImportPanel() {
  const sendCommand = useSendCommand();
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [dryRun, setDryRun] = useState<DryRun | null>(null);
  const [partial, setPartial] = useState(false);
  const [applied, setApplied] = useState<ApplyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  async function choose(file: File) {
    setBusy(true);
    setError(null);
    setDryRun(null);
    setApplied(null);
    setPartial(false);
    try {
      const id = await uploadSettingsFile(file);
      setUploadId(id);
      setDryRun((await sendCommand("home", "import_settings_dryrun", { upload_id: id })) as DryRun);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!dryRun || !uploadId) return;
    setBusy(true);
    setError(null);
    try {
      const result = (await sendCommand("home", "import_settings_apply", {
        upload_id: uploadId,
        token: dryRun.token,
        apply_partial: partial,
      })) as ApplyResult;
      setApplied(result);
      setDryRun(null);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function dropped(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void choose(file);
  }

  const blocked = dryRun !== null && dryRun.needs_partial && !partial;
  return (
    <>
      <div
        className={dragging ? "drop-zone drop-zone-active" : "drop-zone"}
        data-testid="drop-zone"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={dropped}
      >
        <label>
          Drop a settings file here, or choose one:{" "}
          <input
            type="file"
            accept=".json,application/json"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void choose(file);
              event.target.value = "";
            }}
          />
        </label>
      </div>
      {error && (
        <div className="notice notice-error" role="alert">
          {error}
        </div>
      )}
      {dryRun && (
        <div data-testid="dry-run">
          <p>
            Exported from <strong>{dryRun.hostname || "?"}</strong> (Baja Studio{" "}
            {dryRun.app_version}) on {new Date(dryRun.exported_at).toLocaleString()}.
            {dryRun.has_changes ? " Nothing changes until you press Apply." : " Nothing to change."}
          </p>
          {dryRun.sections.map((section) => (
            <SectionDiff key={section.section} plan={section} />
          ))}
          {dryRun.needs_partial && (
            <label className="field-inline">
              <input
                type="checkbox"
                checked={partial}
                onChange={(event) => setPartial(event.target.checked)}
              />{" "}
              Apply partial: import the other modules and skip the ones marked “cannot import”
            </label>
          )}
          <div className="button-row">
            <button onClick={() => void apply()} disabled={busy || blocked || !dryRun.has_changes}>
              Apply
            </button>
          </div>
        </div>
      )}
      {applied && <ApplyResults result={applied} />}
    </>
  );
}

const STATUS_TEXT: Record<SectionPlan["status"], string> = {
  changes: "changes",
  unchanged: "no change",
  invalid: "cannot import",
  unknown: "skipped: unknown here",
};

function SectionDiff({ plan }: { plan: SectionPlan }) {
  const count = plan.added.length + plan.removed.length + plan.changed.length;
  return (
    <details
      className="import-section"
      open={plan.status === "invalid"}
      data-testid={`import-${plan.section}`}
    >
      <summary>
        <strong>{plan.section}</strong>: {STATUS_TEXT[plan.status]}
        {count > 0 ? ` (${count})` : ""}
      </summary>
      {plan.messages.map((message) => (
        <div key={message} className={plan.status === "invalid" ? "notice notice-error" : "notice"}>
          {message}
        </div>
      ))}
      <ChangeList title="Added (new here, or replacing a default)" changes={plan.added} />
      <ChangeList title="Changed" changes={plan.changed} />
      <ChangeList title="Removed" changes={plan.removed} />
      {plan.secrets_to_enter.length > 0 && (
        <div className="muted">Enter again after importing: {plan.secrets_to_enter.join(", ")}</div>
      )}
    </details>
  );
}

function ChangeList({ title, changes }: { title: string; changes: KeyChange[] }) {
  if (changes.length === 0) return null;
  return (
    <>
      <div className="muted">{title}</div>
      <ul className="import-changes">
        {changes.map((change) => (
          <li key={change.key}>
            <code>{change.key}</code>:{" "}
            {title === "Changed"
              ? `${shownValue(change.old)} → ${shownValue(change.new)}`
              : shownValue(title === "Removed" ? change.old : change.new)}
            {change.secret && " (secret)"}
          </li>
        ))}
      </ul>
    </>
  );
}

function ApplyResults({ result }: { result: ApplyResult }) {
  return (
    <div data-testid="apply-results">
      <div className="notice" role="status">
        Imported. Every module has taken its new settings; nothing needs a restart.
      </div>
      <ul>
        {result.results.map((item) => (
          <li key={item.section}>
            {item.section}: {item.result}
          </li>
        ))}
      </ul>
      {result.secrets_to_enter.length > 0 && (
        <div className="notice notice-error" data-testid="secrets-to-enter">
          Enter these again in their settings pages: {result.secrets_to_enter.join(", ")}
        </div>
      )}
    </div>
  );
}
