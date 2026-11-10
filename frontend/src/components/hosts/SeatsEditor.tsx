/**
 * The four seats (WP9 §2): screen name, colour, on/off and the mixer
 * channel of the seat's microphone (for the audio card). Each change is
 * saved at once by a command, so it is in the command log.
 */

import { useState } from "react";

import { INPUTS } from "../../lib/audio";
import type { HostsState, Seat } from "../../lib/hosts";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

export function SeatsEditor({ state, run }: { state: HostsState; run: Run }) {
  return (
    <section aria-label="Seats">
      <h2>Seats</h2>
      <table className="dense-table">
        <thead>
          <tr>
            <th>Seat</th>
            <th>Screen name</th>
            <th>Colour</th>
            <th>On</th>
            <th>Mic channel</th>
            <th>Screen</th>
          </tr>
        </thead>
        <tbody>
          {state.seats.map((seat) => (
            <SeatRow key={seat.id} seat={seat} run={run} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function SeatRow({ seat, run }: { seat: Seat; run: Run }) {
  // Edited locally and saved on Enter or leaving the field, not on every key.
  const [name, setName] = useState(seat.screen_name);
  const [savedName, setSavedName] = useState(seat.screen_name);
  if (seat.screen_name !== savedName) {
    // Someone else renamed the seat: follow them.
    setSavedName(seat.screen_name);
    setName(seat.screen_name);
  }
  const saveName = () => {
    if (name.trim() && name !== seat.screen_name) {
      void run("set_seat_name", { seat_id: seat.id, screen_name: name });
    }
  };
  const change = (args: Record<string, unknown>) =>
    void run("set_seat", { seat_id: seat.id, ...args });
  return (
    <tr>
      <td>{seat.id}</td>
      <td>
        <input
          aria-label={`Seat ${seat.id} name`}
          maxLength={24}
          value={name}
          onChange={(event) => setName(event.target.value)}
          onBlur={saveName}
          onKeyDown={(event) => {
            if (event.key === "Enter") saveName();
          }}
        />
      </td>
      <td>
        <input
          type="color"
          aria-label={`Seat ${seat.id} colour`}
          value={seat.color}
          onChange={(event) => change({ color: event.target.value })}
        />
      </td>
      <td>
        <input
          type="checkbox"
          aria-label={`Seat ${seat.id} on`}
          checked={seat.enabled}
          onChange={(event) => change({ enabled: event.target.checked })}
        />
      </td>
      <td>
        <select
          aria-label={`Seat ${seat.id} mic channel`}
          value={seat.audio_channel ?? ""}
          onChange={(event) => change({ audio_channel: event.target.value || null })}
        >
          <option value="">none</option>
          {INPUTS.map((channel) => (
            <option key={channel} value={channel}>
              {channel}
            </option>
          ))}
        </select>
      </td>
      <td>
        <span className={seat.online ? "badge badge-ok" : "badge badge-offline"}>
          {seat.online ? `online${seat.screens > 1 ? ` ×${seat.screens}` : ""}` : "offline"}
        </span>
      </td>
    </tr>
  );
}
