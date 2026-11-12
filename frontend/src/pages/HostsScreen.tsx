/**
 * The hosts screen (/hosts): a full-screen, read-mostly display for the
 * monitor in front of the hosts (WP9 brief §2, §5). No navigation bar: it
 * runs in a kiosk-mode browser window or on a tablet.
 *
 * On first open it asks which seats this screen is for (kept in
 * localStorage), unless the address names them (`/hosts?seats=1,2,3,4`, how
 * the rehearsal rig opens it). Then: the cards production chose, in its order, the pushed
 * card first; the messages card under them. A red border means the screen
 * is not live (`useHostsLink`): what it shows may be old. Reconnecting
 * brings a fresh snapshot, so missed messages appear by themselves.
 *
 * The screen tells the server which messages it has shown (`mark_shown`),
 * so production sees them delivered.
 *
 * WP9b: under the messages, a host can write to production. Tapping the
 * audio card opens the seat's personal-mix overlay (`SeatMixOverlay`), and
 * a pad or knob changing one of this screen's seats' mixes shows a short
 * toast ("Ana: PC AUDIO +2 dB"), so there is feedback with the overlay shut.
 */

import { useEffect, useRef, useState } from "react";

import { HostsCardFrame } from "../components/hosts/cards";
import { HostComposer } from "../components/hosts/HostComposer";
import { HostsMessages } from "../components/hosts/HostsMessages";
import { SeatMixOverlay } from "../components/hosts/SeatMixOverlay";
import { playChime } from "../lib/chime";
import {
  addressedSeats,
  cardsToShow,
  loadScreenSeats,
  saveScreenSeats,
  seatName,
  seatsFromUrl,
  valueAt,
  type HostsState,
  type Seat,
  type SeatId,
} from "../lib/hosts";
import { formatOffset, PAD_TOAST_MS, type SeatAudioView } from "../lib/seatAudio";
import { useStateTree, useSendCommand } from "../lib/stateHooks";
import { useHostsLink } from "../lib/useHostsLink";

export function HostsScreen() {
  const tree = useStateTree();
  const state = tree.hosts as HostsState | undefined;
  const sendCommand = useSendCommand();
  const [screenSeats, setScreenSeats] = useState<SeatId[] | null>(() => {
    // `/hosts?seats=1,2` chooses (and remembers) the seats without the picker.
    const fromUrl = seatsFromUrl(window.location.search);
    if (fromUrl === null) return loadScreenSeats(window.localStorage);
    saveScreenSeats(window.localStorage, fromUrl);
    return fromUrl;
  });
  const [choosing, setChoosing] = useState(screenSeats === null);
  const stale = useHostsLink(choosing ? null : screenSeats);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const seatAudio = valueAt(tree, "audio.hosts_view") as SeatAudioView | undefined;
  const [mixSeat, setMixSeat] = useState<SeatId | null>(null);

  useDeliveryReceipts(state, choosing ? null : screenSeats);
  useChime(state, choosing ? null : screenSeats);
  const padToast = usePadToast(seatAudio, state?.seats ?? [], choosing ? null : screenSeats);

  const run = (command: string, args: Record<string, unknown>) =>
    sendCommand("hosts", command, args).catch(() => undefined);
  const runAudio = (command: string, args: Record<string, unknown>) =>
    sendCommand("audio", command, args).catch(() => undefined);

  const classes = stale ? "hosts-screen hosts-stale" : "hosts-screen";
  if (!state) {
    return (
      <div className={classes} data-testid="hosts-screen">
        <p className="hosts-quiet">Waiting for the studio service…</p>
      </div>
    );
  }
  if (choosing || screenSeats === null) {
    return (
      <div className={classes} data-testid="hosts-screen">
        <SeatPicker
          seats={state.seats}
          initial={screenSeats ?? []}
          onChosen={(chosen) => {
            saveScreenSeats(window.localStorage, chosen);
            setScreenSeats(chosen);
            setChoosing(false);
          }}
        />
      </div>
    );
  }
  const { grid, pinned } = cardsToShow(state);
  return (
    <div className={classes} data-testid="hosts-screen">
      {stale && (
        <div className="hosts-stale-banner" role="alert">
          Not live: reconnecting to the studio…
        </div>
      )}
      <div className="hosts-grid">
        {grid.map((card) => (
          <HostsCardFrame
            key={card.id}
            card={card}
            value={valueAt(tree, card.state_path)}
            seats={state.seats}
            nowMs={nowMs}
            screenSeats={screenSeats}
            onOpenSeatMix={setMixSeat}
            pushed={card.id === state.pushed_card?.id}
          />
        ))}
      </div>
      {pinned.map((card) => (
        <section key={card.id} className="hosts-card hosts-card-messages" aria-label={card.title}>
          <HostsMessages state={state} screenSeats={screenSeats} nowMs={nowMs} run={run} />
          <HostComposer state={state} screenSeats={screenSeats} run={run} />
        </section>
      ))}
      {mixSeat !== null && seatAudio && (
        <SeatMixOverlay
          seatId={mixSeat}
          seatName={seatName(mixSeat, state.seats)}
          color={state.seats.find((seat) => seat.id === mixSeat)?.color ?? "#d8dce2"}
          view={seatAudio}
          run={runAudio}
          onClose={() => setMixSeat(null)}
        />
      )}
      {padToast && (
        <div className="hosts-pad-toast" role="status" data-testid="pad-toast">
          {padToast}
        </div>
      )}
      <div className="hosts-corner">
        <span className="hosts-subtle">{seatNames(state.seats, screenSeats)}</span>
        <button onClick={() => setChoosing(true)}>Seats</button>
        <button onClick={() => void document.documentElement.requestFullscreen?.()}>
          Fullscreen
        </button>
      </div>
    </div>
  );
}

interface SeatPickerProps {
  seats: Seat[];
  initial: SeatId[];
  onChosen: (seats: SeatId[]) => void;
}

/** Which seats this screen is for: one host, or several sharing a monitor. */
function SeatPicker({ seats, initial, onChosen }: SeatPickerProps) {
  const [chosen, setChosen] = useState<SeatId[]>(initial);
  const toggle = (seat: SeatId) =>
    setChosen((current) =>
      current.includes(seat) ? current.filter((id) => id !== seat) : [...current, seat].sort(),
    );
  return (
    <div className="hosts-seat-picker">
      <h1>Who is at this screen?</h1>
      <div className="hosts-seat-options">
        {seats
          .filter((seat) => seat.enabled)
          .map((seat) => (
            <button
              key={seat.id}
              aria-pressed={chosen.includes(seat.id)}
              className={chosen.includes(seat.id) ? "hosts-seat chosen" : "hosts-seat"}
              style={{ borderColor: seat.color, color: seat.color }}
              onClick={() => toggle(seat.id)}
            >
              {seat.screen_name || `Seat ${seat.id}`}
            </button>
          ))}
      </div>
      <button
        className="hosts-seat-done"
        disabled={chosen.length === 0}
        onClick={() => onChosen(chosen)}
      >
        Done
      </button>
    </div>
  );
}

function seatNames(seats: Seat[], ids: SeatId[]): string {
  return ids
    .map((id) => seats.find((seat) => seat.id === id)?.screen_name ?? `seat ${id}`)
    .join(" · ");
}

/** Tell the server once per message and seat that this screen has shown it. */
function useDeliveryReceipts(state: HostsState | undefined, seats: SeatId[] | null) {
  const sendCommand = useSendCommand();
  const reported = useRef(new Set<string>());
  useEffect(() => {
    if (!state || seats === null) return;
    for (const message of state.messages) {
      const already = new Set(message.shown.map((entry) => entry.seat_id));
      const toReport = addressedSeats(message, seats).filter(
        (seat) => !already.has(seat) && !reported.current.has(`${message.id}:${seat}`),
      );
      if (toReport.length === 0) continue;
      for (const seat of toReport) reported.current.add(`${message.id}:${seat}`);
      sendCommand("hosts", "mark_shown", { message_id: message.id, seat_ids: toReport }).catch(
        () => {
          // Not delivered after all: try again with the next state change.
          for (const seat of toReport) reported.current.delete(`${message.id}:${seat}`);
        },
      );
    }
  }, [state, seats, sendCommand]);
}

/** A soft two-note chime for a new message to this screen's seats, when production turned it on. */
function useChime(state: HostsState | undefined, seats: SeatId[] | null) {
  const newestSeen = useRef<number | null>(null);
  useEffect(() => {
    if (!state) return;
    const newest = state.messages.at(-1)?.id ?? 0;
    const previous = newestSeen.current;
    newestSeen.current = newest;
    // Opening the screen is not news: only messages after that chime.
    if (previous === null || seats === null || !state.config.chime) return;
    const fresh = state.messages.filter(
      (message) => message.id > previous && addressedSeats(message, seats).length > 0,
    );
    if (fresh.length > 0) playChime();
  }, [state, seats]);
}

/**
 * "Ana: PC AUDIO +2 dB" for a few seconds when a pad or knob (not the
 * overlay) moved one of this screen's seats' offsets; the first value seen
 * after opening is not news.
 */
function usePadToast(
  view: SeatAudioView | undefined,
  seats: Seat[],
  screenSeats: SeatId[] | null,
): string | null {
  const change = view?.pad_change ?? null;
  const seq = change?.seq ?? 0;
  // The change already there when audio's view first arrived, and the last one whose
  // toast timed out: neither shows. `seq` counts every change, so "+2 dB" twice is two toasts.
  const [baseline, setBaseline] = useState<number | null>(null);
  const [expiredSeq, setExpiredSeq] = useState<number | null>(null);
  if (baseline === null && view !== undefined) setBaseline(seq);
  const visible =
    change !== null &&
    baseline !== null &&
    seq > baseline &&
    seq !== expiredSeq &&
    screenSeats !== null &&
    screenSeats.includes(change.seat_id as SeatId);
  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(() => setExpiredSeq(seq), PAD_TOAST_MS);
    return () => clearTimeout(timer);
  }, [visible, seq]);
  if (!visible) return null;
  const name = seatName(change.seat_id as SeatId, seats);
  return `${name}: ${change.label} ${formatOffset(change.offset_db)}`;
}
