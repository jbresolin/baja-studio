/**
 * The hosts screen's cards, registered by name (WP9 §3).
 *
 * A module declares a card with a `component` name and a `state_path`; the
 * screen looks the name up in `CARD_COMPONENTS` and hands the component the
 * value at that path. A card from a later work package (standings, team
 * facts) is one more entry here plus its declaration in the module that
 * owns the data. Everything is read-only and drawn large: hosts read it from
 * 1.5 m away.
 */

import type { ReactElement } from "react";

import type { AudioState } from "../../lib/audio";
import { COUNTDOWN_AMBER_AT_SECONDS, streamTime, type CardView, type Seat } from "../../lib/hosts";
import { formatDuration, formatKnownDuration, type HostsView } from "../../lib/playout";

export interface CardProps {
  card: CardView;
  /** The value at the card's `state_path`, undefined while its module is off. */
  value: unknown;
  seats: Seat[];
  nowMs: number;
}

export const CARD_COMPONENTS: Record<string, (props: CardProps) => ReactElement> = {
  now_playing: NowPlayingCard,
  next_up: NextUpCard,
  audio_state: AudioCard,
  clock: ClockCard,
  placeholder: PlaceholderCard,
};

/** The card's frame and title around what the component draws. */
export function HostsCardFrame({ card, ...rest }: CardProps & { pushed: boolean }) {
  const Component = CARD_COMPONENTS[card.component] ?? UnknownCard;
  return (
    <section
      className={rest.pushed ? "hosts-card hosts-card-pushed" : "hosts-card"}
      aria-label={card.title}
      data-card={card.id}
    >
      <h2 className="hosts-card-title">{card.title}</h2>
      <Component card={card} {...rest} />
    </section>
  );
}

function NowPlayingCard({ value }: CardProps) {
  const view = value as HostsView | undefined;
  if (!view?.on_air) {
    return (
      <div className="hosts-quiet">
        <span className="hosts-live-badge">live</span>
      </div>
    );
  }
  const remaining = view.on_air.remaining_s;
  const amber = remaining !== null && remaining <= COUNTDOWN_AMBER_AT_SECONDS;
  return (
    <div>
      <div className="hosts-big-label">{view.on_air.label}</div>
      <div
        className={amber ? "hosts-countdown hosts-countdown-amber" : "hosts-countdown"}
        data-testid="hosts-countdown"
      >
        {remaining === null ? "–" : `−${formatDuration(remaining)}`}
      </div>
    </div>
  );
}

function NextUpCard({ value }: CardProps) {
  const view = value as HostsView | undefined;
  if (!view || view.next.length === 0) return <div className="hosts-quiet">Queue empty</div>;
  return (
    <div>
      <ol className="hosts-next">
        {view.next.map((item, index) => (
          <li key={index}>
            <span>{item.label}</span>
            <span className="hosts-duration">{formatKnownDuration(item.duration_s)}</span>
          </li>
        ))}
      </ol>
      <div className="hosts-subtle">queue {formatDuration(view.queue_remaining_s)}</div>
    </div>
  );
}

function AudioCard({ card, value, seats }: CardProps) {
  const audio = value as AudioState | undefined;
  if (!audio) return <div className="hosts-quiet">Audio is off</div>;
  const mixerUp = audio.mixer.connection === "ok";
  const talkbackIds = (card.options.talkback_macros as string[] | undefined) ?? [];
  const talkback = talkbackIds.some((id) => audio.macros[id]?.active);
  const withMics = seats.filter((seat) => seat.enabled && seat.audio_channel !== null);
  return (
    <div>
      {!mixerUp && <div className="hosts-subtle">Mixer {audio.mixer.connection}</div>}
      <ul className="hosts-mics">
        {withMics.map((seat) => {
          const muted = audio.channels[seat.audio_channel!]?.muted;
          const word =
            !mixerUp || muted === null || muted === undefined ? "?" : muted ? "MUTED" : "LIVE";
          return (
            <li
              key={seat.id}
              className={word === "LIVE" ? "hosts-mic hosts-mic-live" : "hosts-mic"}
              style={{ borderColor: seat.color }}
            >
              <span style={{ color: seat.color }}>{seat.screen_name || `Seat ${seat.id}`}</span>
              <strong>{word}</strong>
            </li>
          );
        })}
      </ul>
      {talkback && (
        <div className="hosts-talkback" role="status">
          Talkback → {String(card.options.talkback_label ?? "camera op")}
        </div>
      )}
    </div>
  );
}

function ClockCard({ value, nowMs }: CardProps) {
  const marker = (value as { stream_start_marker: string | null } | undefined)?.stream_start_marker;
  const sinceStart = streamTime(marker ?? null, nowMs);
  return (
    <div>
      <div className="hosts-clock">{new Date(nowMs).toLocaleTimeString([], { hour12: false })}</div>
      <div className="hosts-subtle" data-testid="stream-time">
        {sinceStart === null ? "stream start not marked" : `on air ${sinceStart}`}
      </div>
    </div>
  );
}

function PlaceholderCard() {
  return <div className="hosts-quiet">Not available yet</div>;
}

function UnknownCard({ card }: CardProps) {
  return <div className="hosts-quiet">This screen cannot draw “{card.component}”; reload it.</div>;
}
