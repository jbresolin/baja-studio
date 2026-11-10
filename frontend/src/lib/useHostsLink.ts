/**
 * The hosts screen's link to the studio: it says which seats it shows, every
 * 2 s, and works out whether what it shows may be stale.
 *
 * Hosts must know when the screen is not live (brief §2), and a pulled
 * network cable can leave a WebSocket "open" for a long time. So the screen
 * does not trust the socket's status alone: every `register_screen` must be
 * answered, and with no answer for `STALE_AFTER_MS` the screen is stale
 * (red border) even while the socket still claims to be open. The same
 * repeats tell production which seats are online.
 */

import { useEffect, useState } from "react";

import type { SeatId } from "./hosts";
import { useConnectionStatus, useSendCommand } from "./stateHooks";

export const REGISTER_EVERY_MS = 2000;
export const STALE_AFTER_MS = 4000;
const CHECK_EVERY_MS = 500;

/** Keep registering `seats` (null: not chosen yet) and return whether the screen is stale. */
export function useHostsLink(seats: SeatId[] | null): boolean {
  const sendCommand = useSendCommand();
  const status = useConnectionStatus();
  const [lastAnswerAt, setLastAnswerAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  // A string, so a new array with the same seats does not restart the repeats.
  const seatsKey = seats === null ? null : seats.join(",");

  useEffect(() => {
    if (status !== "open" || seatsKey === null) return;
    const seatIds = seatsKey.split(",").filter(Boolean).map(Number);
    const register = () => {
      sendCommand("hosts", "register_screen", { seats: seatIds }).then(
        () => setLastAnswerAt(Date.now()),
        () => undefined,
      );
    };
    register();
    const timer = setInterval(register, REGISTER_EVERY_MS);
    return () => clearInterval(timer);
  }, [status, seatsKey, sendCommand]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CHECK_EVERY_MS);
    return () => clearInterval(timer);
  }, []);

  if (status !== "open") return true;
  // Just (re)connected and the first answer is on its way: give it the same grace.
  if (lastAnswerAt === null) return false;
  return now - lastAnswerAt > STALE_AFTER_MS;
}
