/**
 * One strip on the audio page: label in its colour, fader with dB readout,
 * mute. In a bus view the fader is the strip's send to that bus, with its
 * link state (a paused link can be relinked from here).
 */

import { formatDb, type ChannelView, type LinkState } from "../../lib/audio";
import { Fader } from "./Fader";

export interface ChannelStripProps {
  channel: string;
  label: string;
  color: string;
  view: ChannelView | undefined;
  /** The bus whose send this strip shows, or null for the main fader. */
  bus: string | null;
  linkState?: LinkState;
  /** "device:mvave", "web:ab12"... who last moved this fader. */
  lastSource?: string;
  disabled: boolean;
  onLevel: (level: number, dragging: boolean) => void;
  onMute: () => void;
  onRelink?: () => void;
}

export function ChannelStrip(props: ChannelStripProps) {
  const { channel, label, color, view, bus, linkState, lastSource, disabled } = props;
  const level = bus === null ? (view?.fader ?? null) : (view?.sends?.[bus] ?? null);
  const muted = view?.muted === true;

  return (
    <section className="strip" data-testid={`strip-${channel}`}>
      <div className="strip-label" style={{ borderColor: color }} title={view?.name ?? undefined}>
        {label}
      </div>
      <Fader
        label={bus === null ? `${label} fader` : `${label} send`}
        level={level}
        color={color}
        disabled={disabled}
        onChange={props.onLevel}
      />
      <div className="strip-db">{formatDb(level)}</div>
      {bus !== null && linkState && linkState !== "unlinked" && (
        <button
          type="button"
          className={`link-chip link-${linkState}`}
          onClick={props.onRelink}
          disabled={linkState !== "paused" || disabled}
          title={linkState === "paused" ? "Follow the main fader again" : "Follows the main fader"}
        >
          {linkState === "paused" ? "paused · relink" : "linked"}
        </button>
      )}
      <button
        type="button"
        className={muted ? "mute-button muted" : "mute-button"}
        aria-pressed={muted}
        onClick={props.onMute}
        disabled={disabled}
      >
        {muted ? "Muted" : "Mute"}
      </button>
      {lastSource && <div className="strip-source muted">{lastSource.replace(/:.*/, "")}</div>}
    </section>
  );
}
