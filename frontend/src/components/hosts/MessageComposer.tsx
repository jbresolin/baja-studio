/**
 * Production's message composer (WP9 §4): target (a seat or everyone),
 * kind, canned messages and the text. `compact` is the one-line box on the
 * playout page (target + text + send), so the operator never leaves it.
 */

import { useState } from "react";

import type { HostsState, MessageKind, MessageTarget } from "../../lib/hosts";

type Run = (command: string, args: Record<string, unknown>) => Promise<unknown>;

interface MessageComposerProps {
  state: HostsState;
  run: Run;
  compact?: boolean;
}

const KINDS: MessageKind[] = ["info", "question", "urgent"];

export function MessageComposer({ state, run, compact = false }: MessageComposerProps) {
  const seats = state.seats.filter((seat) => seat.enabled);
  const [target, setTarget] = useState<MessageTarget>("all");
  const [kind, setKind] = useState<MessageKind>("info");
  const [text, setText] = useState("");

  const send = async (body: string) => {
    const trimmed = body.trim();
    if (!trimmed) return;
    const sent = await run("send_message", { to: target, text: trimmed, kind });
    if (sent !== undefined) setText("");
  };

  return (
    <form
      className={compact ? "hosts-composer hosts-composer-compact" : "hosts-composer"}
      aria-label="Message hosts"
      onSubmit={(event) => {
        event.preventDefault();
        void send(text);
      }}
    >
      <select
        aria-label="To"
        value={String(target)}
        onChange={(event) =>
          setTarget(
            event.target.value === "all" ? "all" : (Number(event.target.value) as MessageTarget),
          )
        }
      >
        <option value="all">Everyone</option>
        {seats.map((seat) => (
          <option key={seat.id} value={seat.id}>
            {seat.screen_name || `Seat ${seat.id}`}
            {seat.online ? "" : " (offline)"}
          </option>
        ))}
      </select>
      {!compact && (
        <select
          aria-label="Kind"
          value={kind}
          onChange={(event) => setKind(event.target.value as MessageKind)}
        >
          {KINDS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      )}
      <input
        aria-label="Message"
        placeholder={compact ? "Message hosts…" : "Message"}
        maxLength={200}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <button type="submit" disabled={!text.trim()}>
        Send
      </button>
      {!compact && (
        <div className="button-row hosts-canned">
          {state.config.canned_messages.map((canned) => (
            <button key={canned} type="button" onClick={() => void send(canned)}>
              {canned}
            </button>
          ))}
        </div>
      )}
      {compact && state.config.canned_messages.length > 0 && (
        <select
          aria-label="Canned message"
          value=""
          onChange={(event) => {
            if (event.target.value) void send(event.target.value);
          }}
        >
          <option value="">Canned…</option>
          {state.config.canned_messages.map((canned) => (
            <option key={canned} value={canned}>
              {canned}
            </option>
          ))}
        </select>
      )}
    </form>
  );
}
