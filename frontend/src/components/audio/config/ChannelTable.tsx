/**
 * Setup: each strip's label, colour, and whether the audio page shows it.
 * The name typed on the mixer is shown next to it as a hint.
 */

import { CHANNEL_IDS, plainName, type AudioState } from "../../../lib/audio";
import type { EditSettings } from "./DevicesSection";
import type { AudioSettingsValues } from "../../../lib/audio";

export function ChannelTable({
  draft,
  live,
  edit,
}: {
  draft: AudioSettingsValues;
  live: AudioState | undefined;
  edit: EditSettings;
}) {
  const update = (channel: string, change: Partial<AudioSettingsValues["channels"][string]>) =>
    edit((d) => ({
      ...d,
      channels: { ...d.channels, [channel]: { ...d.channels[channel]!, ...change } },
    }));

  return (
    <table className="config-list" data-testid="channel-table">
      <thead>
        <tr>
          <th>Strip</th>
          <th>Label</th>
          <th>Colour</th>
          <th>On the audio page</th>
          <th>Name on the mixer</th>
        </tr>
      </thead>
      <tbody>
        {CHANNEL_IDS.map((channel) => {
          const config = draft.channels[channel]!;
          return (
            <tr key={channel}>
              <td>{plainName(channel)}</td>
              <td>
                <input
                  aria-label={`${plainName(channel)} label`}
                  value={config.label}
                  maxLength={24}
                  onChange={(event) => update(channel, { label: event.target.value })}
                />
              </td>
              <td>
                <input
                  type="color"
                  aria-label={`${plainName(channel)} colour`}
                  value={config.color}
                  onChange={(event) => update(channel, { color: event.target.value })}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  aria-label={`${plainName(channel)} visible`}
                  checked={config.visible}
                  onChange={(event) => update(channel, { visible: event.target.checked })}
                />
              </td>
              <td className="muted">{live?.channels[channel]?.name || "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
