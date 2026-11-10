/**
 * A host writes to production (WP9b §1): one-tap phrases production set up
 * ("preciso de 1 min", "pronto"…), or a typed message, optionally urgent.
 * Always addressed to production; hosts never write to each other. A screen
 * shared by several seats picks which seat is speaking.
 */

import { useState } from "react";

import type { HostsState, Seat, SeatId } from "../../lib/hosts";

type Run = (command: string, args: Record<string, unknown>) => Promise<unknown>;

interface HostComposerProps {
  state: HostsState;
  screenSeats: SeatId[];
  run: Run;
}

export function HostComposer({ state, screenSeats, run }: HostComposerProps) {
  const seats = screenSeats
    .map((id) => state.seats.find((seat) => seat.id === id))
    .filter((seat): seat is Seat => seat !== undefined && seat.enabled);
  const [from, setFrom] = useState<SeatId | null>(null);
  const [text, setText] = useState("");
  const [urgent, setUrgent] = useState(false);
  const speaking = seats.find((seat) => seat.id === from) ?? seats[0];
  if (speaking === undefined) return null;

  const send = async (body: string) => {
    const trimmed = body.trim();
    if (!trimmed) return;
    const sent = await run("send_host_message", {
      seat_id: speaking.id,
      text: trimmed,
      kind: urgent ? "urgent" : "info",
    });
    if (sent !== undefined) {
      setText("");
      setUrgent(false);
    }
  };

  return (
    <form
      className="hosts-composer-host"
      aria-label="Message production"
      onSubmit={(event) => {
        event.preventDefault();
        void send(text);
      }}
    >
      {seats.length > 1 && (
        <select
          aria-label="From"
          value={speaking.id}
          onChange={(event) => setFrom(Number(event.target.value) as SeatId)}
        >
          {seats.map((seat) => (
            <option key={seat.id} value={seat.id}>
              {seat.screen_name || `Seat ${seat.id}`}
            </option>
          ))}
        </select>
      )}
      {state.config.host_phrases.map((phrase) => (
        <button
          key={phrase}
          type="button"
          className="hosts-phrase"
          onClick={() => void send(phrase)}
        >
          {phrase}
        </button>
      ))}
      <input
        aria-label="Message to production"
        placeholder="to production…"
        maxLength={200}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <label className="hosts-urgent-toggle">
        <input
          type="checkbox"
          checked={urgent}
          onChange={(event) => setUrgent(event.target.checked)}
        />{" "}
        urgent
      </label>
      <button type="submit" disabled={!text.trim()}>
        Send
      </button>
    </form>
  );
}
