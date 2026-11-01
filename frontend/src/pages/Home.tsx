import { Link } from "react-router-dom";

import { HealthBadge } from "../components/HealthBadge";
import { useModuleState } from "../lib/stateHooks";

interface HomeModuleEntry {
  title: string;
  state: string;
  message: string;
}

interface HomeState {
  modules: Record<string, HomeModuleEntry>;
}

/** The landing page: one card per module with its live health. */
export function Home() {
  const home = useModuleState<HomeState>("home");
  const modules = Object.entries(home?.modules ?? {});

  if (modules.length === 0) {
    return <p className="muted">Waiting for the studio service…</p>;
  }

  return (
    <div className="card-grid">
      {modules.map(([name, entry]) => (
        <Link to={`/modules/${name}`} className="card" key={name} data-testid={`card-${name}`}>
          <div className="card-header">
            <span className="card-title">{entry.title}</span>
            <HealthBadge state={entry.state} />
          </div>
          {entry.message && <div className="card-message">{entry.message}</div>}
        </Link>
      ))}
    </div>
  );
}
