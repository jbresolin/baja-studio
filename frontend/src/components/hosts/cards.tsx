/**
 * The hosts screen's cards, registered by name (WP9 §3).
 *
 * A module declares a card with a `component` name and a `state_path`; the
 * screen looks the name up in `CARD_COMPONENTS` and hands the component the
 * value at that path. A card from a later work package (standings, team
 * facts) is one more entry here plus its declaration in the module that
 * owns the data. Everything is drawn large (hosts read it from 1.5 m away)
 * and read-only, except that the audio card opens a seat's personal-mix
 * overlay (WP9b §3).
 */

import type { ReactElement } from "react";

import {
  COUNTDOWN_AMBER_AT_SECONDS,
  streamTime,
  type CardView,
  type Seat,
  type SeatId,
} from "../../lib/hosts";
import { formatDuration, formatKnownDuration, type HostsView } from "../../lib/playout";
import { seatStateWord, type SeatAudioView } from "../../lib/seatAudio";

export interface CardProps {
  card: CardView;
  /** The value at the card's `state_path`, undefined while its module is off. */
  value: unknown;
  seats: Seat[];
  nowMs: number;
  /** The seats this hosts screen is for. */
  screenSeats: SeatId[];
  /** Open a seat's personal-mix overlay (the audio card's one interaction, WP9b §3). */
  onOpenSeatMix?: (seatId: SeatId) => void;
}

export const CARD_COMPONENTS: Record<string, (props: CardProps) => ReactElement> = {
  now_playing: NowPlayingCard,
  next_up: NextUpCard,
  seat_audio: SeatAudioCard,
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

/**
 * Where each seat's microphone is heard (WP9b §2): LIVE, TO PRODUCTION,
 * MUTED or "not mapped", in the seat's colour, plus "production → you" when
 * a production microphone is open into that seat's in-ears. Audio publishes
 * the facts (`audio.hosts_view`); names and colours are the hosts' seats.
 * This screen's own seats are buttons that open their personal mix.
 */
function SeatAudioCard({ value, seats, screenSeats, onOpenSeatMix }: CardProps) {
  const view = value as SeatAudioView | undefined;
  if (!view) return <div className="hosts-quiet">Audio is off</div>;
  if (!view.mixer_online) {
    return (
      <div className="hosts-quiet" role="status">
        Mixer offline
      </div>
    );
  }
  return (
    <div>
      <ul className="hosts-mics">
        {seats
          .filter((seat) => seat.enabled)
          .map((seat) => {
            const audio = view.seats[String(seat.id)];
            const state = audio?.state ?? null;
            const name = seat.screen_name || `Seat ${seat.id}`;
            const content = (
              <>
                <span style={{ color: seat.color }}>{name}</span>
                <strong>{seatStateWord(state)}</strong>
                {audio?.production_to_you && (
                  <span className="hosts-prod-to-you">production → you</span>
                )}
              </>
            );
            const mine = screenSeats.includes(seat.id) && onOpenSeatMix !== undefined;
            return (
              <li
                key={seat.id}
                className={`hosts-mic hosts-mic-${state ?? "unknown"}`}
                style={{ borderColor: seat.color }}
                data-testid={`seat-audio-${seat.id}`}
              >
                {mine ? (
                  <button
                    className="hosts-mic-open"
                    aria-label={`${name}: ${seatStateWord(state)}, open the in-ear mix`}
                    onClick={() => onOpenSeatMix(seat.id)}
                  >
                    {content}
                  </button>
                ) : (
                  content
                )}
              </li>
            );
          })}
      </ul>
      {view.talkback.active && (
        <div className="hosts-talkback" role="status">
          Talkback → {view.talkback.label}
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
