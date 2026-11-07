import { useState } from "react";
import { Link } from "react-router-dom";

import { HealthBadge } from "../components/HealthBadge";
import type { ReadinessState } from "../lib/readiness";
import { useModuleState, useSendCommand } from "../lib/stateHooks";
import { ReadinessBanner } from "./ReadinessPage";

interface HomeModuleEntry {
  title: string;
  state: string;
  message: string;
}

interface HomeState {
  modules: Record<string, HomeModuleEntry>;
  version?: string;
}

interface ProxiedAppState {
  path_prefix: string;
  label: string;
  reachable: boolean | null;
  error: string;
}

interface PortConflict {
  port: number;
  holder: string;
  fix: string;
}

interface GatewayState {
  caddy?: { port_conflicts?: PortConflict[] };
  tls: {
    mode: "dns01" | "off";
    hostname: string;
    issued: boolean | null;
    expires_at: string | null;
    last_error: string;
  };
  apps: ProxiedAppState[];
  public_base: string | null;
}

interface BundleResult {
  file_name: string;
  url: string;
}

/** The landing page: readiness, one card per module with its live health, the proxied apps, TLS. */
export function Home() {
  const home = useModuleState<HomeState>("home");
  const gateway = useModuleState<GatewayState>("gateway");
  const readiness = useModuleState<ReadinessState>("readiness");
  const modules = Object.entries(home?.modules ?? {});

  if (modules.length === 0) {
    return <p className="muted">Waiting for the studio service…</p>;
  }

  return (
    <div className="home">
      {readiness?.last_run ? (
        <ReadinessBanner run={readiness.last_run} link />
      ) : (
        readiness && (
          <div className="notice" data-testid="readiness-not-run">
            Readiness not checked yet. <Link to="/readiness">Run the checks</Link>
          </div>
        )
      )}

      {/* Caddy cannot start while another program holds its port: say who, and what to do. */}
      {gateway?.caddy?.port_conflicts?.map((conflict) => (
        <div
          className="notice notice-error"
          key={`${conflict.port}-${conflict.holder}`}
          data-testid="gateway-port-conflict"
        >
          <strong>
            Gateway: port {conflict.port} in use by {conflict.holder}.
          </strong>{" "}
          {conflict.fix}
        </div>
      ))}

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

      {gateway && gateway.apps.length > 0 && (
        <>
          <h2>Apps</h2>
          <div className="card-grid">
            {gateway.apps.map((app) => (
              <ProxiedAppCard key={app.path_prefix} app={app} publicBase={gateway.public_base} />
            ))}
          </div>
        </>
      )}

      {gateway && <TlsStatus tls={gateway.tls} />}

      <div className="button-row home-footer">
        <span className="muted" data-testid="version">
          Baja Studio {home?.version ?? ""}
        </span>
        <DiagnosticsButton />
      </div>
    </div>
  );
}

/** An app behind the gateway, linked through the gateway's own address. */
function ProxiedAppCard({ app, publicBase }: { app: ProxiedAppState; publicBase: string | null }) {
  // Without TLS the gateway serves plain HTTP on port 80 of whatever address
  // this page was opened on (its port 8888 is the studio itself, not the gateway).
  const base = publicBase ?? `http://${window.location.hostname}`;
  const state = app.reachable === null ? "stopped" : app.reachable ? "ok" : "offline";
  return (
    <a className="card" href={`${base}${app.path_prefix}/`} data-testid={`app-${app.path_prefix}`}>
      <div className="card-header">
        <span className="card-title">{app.label || app.path_prefix}</span>
        <HealthBadge state={state} />
      </div>
      {!app.reachable && app.error && <div className="card-message">{app.error}</div>}
    </a>
  );
}

function TlsStatus({ tls }: { tls: GatewayState["tls"] }) {
  let text: string;
  let problem = false;
  if (tls.mode === "off") {
    text = "HTTPS is off: the gateway serves plain HTTP on port 80.";
  } else if (tls.issued) {
    const until = tls.expires_at ? new Date(tls.expires_at).toLocaleDateString() : "?";
    text = `HTTPS on ${tls.hostname}: certificate valid until ${until}.`;
  } else if (tls.issued === false) {
    text = `HTTPS on ${tls.hostname}: no valid certificate (${tls.last_error || "not issued yet"}).`;
    problem = true;
  } else {
    text = `HTTPS on ${tls.hostname}: checking the certificate…`;
  }
  return (
    <div className={problem ? "notice notice-error" : "notice"} data-testid="tls-status">
      {text}
    </div>
  );
}

/** Builds a diagnostics zip on the server and offers it for download. */
function DiagnosticsButton() {
  const sendCommand = useSendCommand();
  const [busy, setBusy] = useState(false);
  const [bundle, setBundle] = useState<BundleResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function build() {
    setBusy(true);
    setError(null);
    try {
      setBundle((await sendCommand("home", "diagnostics_bundle")) as BundleResult);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button onClick={() => void build()} disabled={busy}>
        {busy ? "Building…" : "Diagnostics bundle"}
      </button>
      {bundle && (
        <a href={bundle.url} download={bundle.file_name}>
          Download {bundle.file_name}
        </a>
      )}
      {error && <span className="notice notice-error">{error}</span>}
    </>
  );
}
