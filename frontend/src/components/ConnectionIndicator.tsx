import { useConnectionStatus } from "../lib/stateHooks";

const LABELS = { open: "live", connecting: "reconnecting…", closed: "disconnected" } as const;

/** Shows whether the page is receiving live state from the service. */
export function ConnectionIndicator() {
  const status = useConnectionStatus();
  return (
    <span className={`connection connection-${status}`} title="Live connection to the service">
      ● {LABELS[status]}
    </span>
  );
}
