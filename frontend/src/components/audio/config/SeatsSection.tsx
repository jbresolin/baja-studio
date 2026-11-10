/**
 * Setup: the audio side of the hosts' seats (WP9b §2) and their personal
 * mixes (§3). Part of the audio setup draft (explicit Save, like the rest).
 *
 * - The production bus (a host heard there but not on LR is "TO
 *   PRODUCTION" on the hosts screen) and which inputs are production
 *   microphones ("production → you" when one is open into a seat's in-ears).
 * - Per seat: its microphone's input and its in-ear bus. Names and colours
 *   are the hosts page's; they are shown here to tell the seats apart.
 * - Whether the hosts may change their own in-ear mix, and by how much per tap.
 */

import {
  AUX_BUSES,
  SEND_SOURCES,
  channelLabel,
  plainName,
  type AudioSettingsValues,
  type AuxBus,
  type SeatAudioConfig,
} from "../../../lib/audio";
import { CheckField, NumberField, SelectField } from "../../config/fields";

type Edit = (change: (draft: AudioSettingsValues) => AudioSettingsValues) => void;

interface SeatsSectionProps {
  draft: AudioSettingsValues;
  edit: Edit;
  /** The hosts page's seat names by id, when the hosts module is on. */
  seatNames: Record<number, string>;
}

export function SeatsSection({ draft, edit, seatNames }: SeatsSectionProps) {
  const labelled = (channel: string) => {
    const label = channelLabel(draft.channels, channel);
    return label === plainName(channel) ? label : `${label} (${plainName(channel)})`;
  };
  const busOptions = AUX_BUSES.map((bus) => ({ value: bus, label: labelled(bus) }));
  const changeSeat = (seatId: number, change: Partial<SeatAudioConfig>) =>
    edit((d) => ({
      ...d,
      seats: d.seats.map((seat) => (seat.seat_id === seatId ? { ...seat, ...change } : seat)),
    }));
  const toggleProduction = (channel: string, on: boolean) =>
    edit((d) => ({
      ...d,
      production_channels: on
        ? [...d.production_channels, channel]
        : d.production_channels.filter((candidate) => candidate !== channel),
    }));

  return (
    <section className="config-form" data-testid="seats-section">
      <div className="form-grid">
        <SelectField
          label="Production bus"
          value={draft.production_bus}
          options={busOptions}
          help="A host heard here but not on the main mix shows “TO PRODUCTION” on the hosts screen."
          onChange={(bus) => edit((d) => ({ ...d, production_bus: bus as AuxBus }))}
        />
        <CheckField
          label="Hosts may change their own in-ear mix (otherwise the overlay is view-only)"
          checked={draft.hosts_can_adjust_mix}
          onChange={(on) => edit((d) => ({ ...d, hosts_can_adjust_mix: on }))}
        />
        <NumberField
          label="One tap of + or − on the hosts screen (dB)"
          value={draft.personal_mix_step_db}
          min={0.5}
          max={6}
          step={0.5}
          onChange={(value) => edit((d) => ({ ...d, personal_mix_step_db: value ?? 1 }))}
        />
      </div>

      <h3>Production microphones</h3>
      <div className="button-row" role="group" aria-label="Production microphones">
        {SEND_SOURCES.map((channel) => (
          <label key={channel} className="checkbox-chip">
            <input
              type="checkbox"
              checked={draft.production_channels.includes(channel)}
              onChange={(event) => toggleProduction(channel, event.target.checked)}
            />{" "}
            {labelled(channel)}
          </label>
        ))}
      </div>

      <h3>Seats</h3>
      <table className="config-list" data-testid="seat-audio-table">
        <thead>
          <tr>
            <th>Seat</th>
            <th>Microphone</th>
            <th>In-ears</th>
          </tr>
        </thead>
        <tbody>
          {draft.seats.map((seat) => (
            <tr key={seat.seat_id}>
              <th>{seatNames[seat.seat_id] ?? `Seat ${seat.seat_id}`}</th>
              <td>
                <select
                  aria-label={`Seat ${seat.seat_id} microphone`}
                  value={seat.channel ?? ""}
                  onChange={(event) =>
                    changeSeat(seat.seat_id, { channel: event.target.value || null })
                  }
                >
                  <option value="">not mapped</option>
                  {SEND_SOURCES.map((channel) => (
                    <option key={channel} value={channel}>
                      {labelled(channel)}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <select
                  aria-label={`Seat ${seat.seat_id} in-ears`}
                  value={seat.iem_bus}
                  onChange={(event) =>
                    changeSeat(seat.seat_id, { iem_bus: event.target.value as AuxBus })
                  }
                >
                  {busOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
