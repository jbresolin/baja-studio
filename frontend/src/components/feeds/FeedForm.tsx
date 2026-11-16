/**
 * The fields of one feed, for adding a new one or editing a saved one.
 *
 * The password box is write-only, like playout's OBS password: a saved
 * password is never shown (the server only says there is one). Leaving the
 * box empty keeps it; "Remove the password" clears it.
 */

import { useState } from "react";

import { FEED_NAME_PATTERN, SECRET_MASK, type FeedKind, type FeedSettings } from "../../lib/feeds";

interface FeedFormProps {
  initial: FeedSettings;
  /** Names already used by the other feeds (a name must be unique). */
  takenNames: string[];
  submitLabel: string;
  saving: boolean;
  onSubmit: (feed: FeedSettings) => void;
  onCancel?: () => void;
}

const KINDS: FeedKind[] = ["drone", "laptop", "other"];

export function FeedForm({
  initial,
  takenNames,
  submitLabel,
  saving,
  onSubmit,
  onCancel,
}: FeedFormProps) {
  const [name, setName] = useState(initial.name);
  const [label, setLabel] = useState(initial.label);
  const [kind, setKind] = useState<FeedKind>(initial.kind);
  const [record, setRecord] = useState(initial.record);
  const [newPassword, setNewPassword] = useState("");
  const [removePassword, setRemovePassword] = useState(false);
  const hasSavedPassword = initial.password === SECRET_MASK;

  const trimmed = name.trim();
  const problem = !FEED_NAME_PATTERN.test(trimmed)
    ? "Name: lowercase letters, digits, '-' or '_' (e.g. drone, laptop1)."
    : takenNames.includes(trimmed)
      ? `Another feed is already called ${trimmed}.`
      : "";

  function password(): string {
    if (newPassword) return newPassword;
    if (removePassword) return "";
    return initial.password;
  }

  return (
    <form
      className="feed-form command-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (problem) return;
        onSubmit({
          ...initial,
          name: trimmed,
          label: label.trim(),
          kind,
          record,
          password: password(),
        });
      }}
    >
      <input
        aria-label="Feed name"
        placeholder="name (e.g. drone)"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <input
        aria-label="Feed label"
        placeholder="label in OBS (optional)"
        value={label}
        onChange={(event) => setLabel(event.target.value)}
      />
      <select
        aria-label="Feed kind"
        value={kind}
        onChange={(event) => setKind(event.target.value as FeedKind)}
      >
        {KINDS.map((option) => (
          <option key={option}>{option}</option>
        ))}
      </select>
      <label>
        <input
          type="checkbox"
          checked={record}
          onChange={(event) => setRecord(event.target.checked)}
        />{" "}
        record to disk
      </label>
      <input
        aria-label="Feed password"
        type="password"
        autoComplete="new-password"
        placeholder={hasSavedPassword ? "password set (type to change)" : "password (optional)"}
        value={newPassword}
        onChange={(event) => setNewPassword(event.target.value)}
      />
      {hasSavedPassword && (
        <label>
          <input
            type="checkbox"
            checked={removePassword}
            onChange={(event) => setRemovePassword(event.target.checked)}
          />{" "}
          remove the password
        </label>
      )}
      <button type="submit" disabled={saving || Boolean(problem)}>
        {submitLabel}
      </button>
      {onCancel && (
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      )}
      {problem && trimmed && <span className="muted">{problem}</span>}
    </form>
  );
}
