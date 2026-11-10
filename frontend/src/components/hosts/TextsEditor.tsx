/**
 * Canned messages, quick replies, how long a message stays bright, and the
 * chime (WP9 §4). One item per line; Save sends them all at once.
 */

import { useState } from "react";

import type { HostsState } from "../../lib/hosts";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

const lines = (text: string) =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

export function TextsEditor({ state, run }: { state: HostsState; run: Run }) {
  const config = state.config;
  const [canned, setCanned] = useState(config.canned_messages.join("\n"));
  const [replies, setReplies] = useState(config.quick_replies.join("\n"));
  const [expires, setExpires] = useState(String(config.message_expires_s));
  const [chime, setChime] = useState(config.chime);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    const result = await run("set_texts", {
      canned_messages: lines(canned),
      quick_replies: lines(replies),
      message_expires_s: Number(expires),
      chime,
    });
    setSaved(result !== undefined);
  };

  return (
    <section aria-label="Messages setup">
      <h2>Canned messages and replies</h2>
      <div className="form-grid">
        <label className="field">
          Canned messages (one per line)
          <textarea
            rows={6}
            value={canned}
            onChange={(event) => {
              setCanned(event.target.value);
              setSaved(false);
            }}
          />
        </label>
        <label className="field">
          Quick replies for the hosts (one per line)
          <textarea
            rows={6}
            value={replies}
            onChange={(event) => {
              setReplies(event.target.value);
              setSaved(false);
            }}
          />
        </label>
        <label className="field">
          A message fades after (seconds, 0 = never)
          <input
            type="number"
            min={0}
            max={3600}
            value={expires}
            onChange={(event) => {
              setExpires(event.target.value);
              setSaved(false);
            }}
          />
        </label>
        <label className="field">
          <span>
            <input
              type="checkbox"
              checked={chime}
              onChange={(event) => {
                setChime(event.target.checked);
                setSaved(false);
              }}
            />{" "}
            Chime on the hosts screen for a new message
          </span>
        </label>
      </div>
      <div className="button-row">
        <button onClick={() => void save()}>Save</button>
        {saved && <span className="muted">Saved.</span>}
      </div>
    </section>
  );
}
