/**
 * Setup: which inputs' sends follow their main fader (the link matrix).
 *
 * Unlike the rest of the setup this is live routing, changed during the
 * show (a knob pauses a link), so a tap applies at once, like a mute, and
 * is not part of Save. Cells cycle unlinked → linked → paused → unlinked.
 */

import {
  AUX_BUSES,
  channelLabel,
  nextLinkState,
  SEND_SOURCES,
  type AudioSettingsValues,
  type AudioState,
  type LinkState,
} from "../../../lib/audio";

const SYMBOLS: Record<LinkState, string> = { linked: "●", paused: "◐", unlinked: "·" };

export function LinkMatrix({
  draft,
  live,
  run,
}: {
  draft: AudioSettingsValues;
  live: AudioState | undefined;
  run: (command: string, args?: Record<string, unknown>) => void;
}) {
  const links = live?.links ?? {};
  // Visible inputs, plus any that are linked though hidden.
  const rows = SEND_SOURCES.filter((channel) => draft.channels[channel]?.visible || links[channel]);

  return (
    <section className="config-form">
      <p className="muted">
        ● linked: the send follows the main fader. ◐ paused: kept, not following (a knob or D200
        nudge pauses it). Changes apply at once.
      </p>
      <table className="config-list link-matrix" data-testid="link-matrix">
        <thead>
          <tr>
            <th />
            {AUX_BUSES.map((bus) => (
              <th key={bus}>{channelLabel(draft.channels, bus)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((channel) => (
            <tr key={channel}>
              <th>{channelLabel(draft.channels, channel)}</th>
              {AUX_BUSES.map((bus) => {
                const state = links[channel]?.[bus] ?? "unlinked";
                const name = `${channelLabel(draft.channels, channel)} to ${channelLabel(draft.channels, bus)}`;
                return (
                  <td key={bus}>
                    <button
                      type="button"
                      className={`link-cell link-${state}`}
                      aria-label={`${name}: ${state}`}
                      onClick={() => run("set_link", { channel, bus, state: nextLinkState(state) })}
                    >
                      {SYMBOLS[state]}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
