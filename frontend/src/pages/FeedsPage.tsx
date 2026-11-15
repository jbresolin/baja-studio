/**
 * The Feeds page (/feeds, WP14): remote pictures (the drone, trackside
 * laptops) arriving through the bundled MediaMTX.
 *
 * From the top: the feeds service's health (and, when it is not answering,
 * why: a port taken by another program, its last log lines); senders turned
 * away for an unknown name; one card per feed (FeedCard); a form to add a
 * feed; and the rest of the feeds settings (ports, accept any name,
 * recordings folder) through the generic settings form, which leaves out
 * the feed list (`managed_elsewhere`), so no field is edited in two places.
 *
 * The page changes OBS only through a card's Add to OBS button.
 */

import { useState } from "react";

import { FeedCard } from "../components/feeds/FeedCard";
import { FeedForm } from "../components/feeds/FeedForm";
import { HealthBadge } from "../components/HealthBadge";
import {
  describePicture,
  formatBitrate,
  formatFreeSpace,
  newFeedId,
  type FeedSettings,
  type FeedsState,
} from "../lib/feeds";
import { useModuleState } from "../lib/stateHooks";
import { useFeedsSettings } from "../lib/useFeedsSettings";
import { SectionEditor } from "./Settings";

interface HomeEntry {
  title: string;
  state: string;
  message: string;
}

function emptyFeed(): FeedSettings {
  return { id: newFeedId(), name: "", label: "", kind: "drone", record: false, password: "" };
}

export function FeedsPage() {
  const feeds = useModuleState<FeedsState>("feeds");
  const home = useModuleState<{ modules: Record<string, HomeEntry> }>("home");
  const settings = useFeedsSettings();
  // A new blank form after each feed added.
  const [newFeed, setNewFeed] = useState<FeedSettings>(emptyFeed);
  const health = home?.modules.feeds;
  const saved = settings.feeds ?? [];

  function namesExcept(id: string): string[] {
    return saved.filter((feed) => feed.id !== id).map((feed) => feed.name);
  }

  async function replace(id: string, feed: FeedSettings | null) {
    const next = saved.flatMap((item) => (item.id !== id ? [item] : feed ? [feed] : []));
    await settings.saveFeeds(next);
  }

  return (
    <div className="module-page feeds-page">
      <div className="page-header">
        <h1>Feeds</h1>
        {health && <HealthBadge state={health.state} />}
      </div>
      {health?.message && <div className="card-message">{health.message}</div>}
      {feeds && <ServicePanel feeds={feeds} />}

      {settings.notice && (
        <div
          className={settings.notice.kind === "saved" ? "notice" : "notice notice-error"}
          role="status"
        >
          {settings.notice.text}
        </div>
      )}

      {feeds && feeds.refused.length > 0 && (
        <div className="notice notice-error" data-testid="refused-senders">
          Refused (unknown feed name):{" "}
          {feeds.refused.map((item) => `${item.name} at ${item.at.slice(11)}`).join(", ")}. Check
          the name typed in the sender, or add it as a feed.
        </div>
      )}

      {(feeds?.feeds ?? []).length === 0 && (
        <p className="muted">No feeds yet. Add the drone and each laptop below.</p>
      )}
      <div className="feed-grid">
        {(feeds?.feeds ?? []).map((card) => (
          <FeedCard
            key={card.id}
            card={card}
            saved={saved.find((feed) => feed.id === card.id) ?? null}
            takenNames={namesExcept(card.id)}
            saving={settings.saving}
            onSave={(feed) => void replace(card.id, feed)}
            onRemove={() => void replace(card.id, null)}
          />
        ))}
      </div>

      {feeds && feeds.other_paths.length > 0 && (
        <>
          <h2>Other senders (accept any name is on)</h2>
          <ul data-testid="other-senders">
            {feeds.other_paths.map((item) => (
              <li key={item.name}>
                {item.name}: {item.protocol} {describePicture(item)}{" "}
                {formatBitrate(item.bitrate_kbps)}
              </li>
            ))}
          </ul>
        </>
      )}

      <h2>Add a feed</h2>
      <FeedForm
        key={newFeed.id}
        initial={newFeed}
        takenNames={saved.map((feed) => feed.name)}
        submitLabel="Add feed"
        saving={settings.saving || settings.feeds === null}
        onSubmit={(feed) => {
          void settings.saveFeeds([...saved, feed]).then((ok) => {
            if (ok) setNewFeed(emptyFeed());
          });
        }}
      />

      <h2>Ports and options</h2>
      <p className="muted">
        Changing a port checks first that it is free: a port in use is refused, and MediaMTX keeps
        running on the old one.
      </p>
      <SectionEditor key={settings.generation} section="feeds" />
    </div>
  );
}

/** The feeds service: running or not, why not, and what MediaMTX printed last. */
function ServicePanel({ feeds }: { feeds: FeedsState }) {
  const { service, config, recordings } = feeds;
  return (
    <section className="feeds-service" data-testid="feeds-service">
      <div>
        {service.answering
          ? `MediaMTX ${service.version ?? ""} running (${service.name}, API ${service.api})`
          : `MediaMTX not answering: ${service.message || "starting"}`}
        {recordings.any_feed_records &&
          ` · recordings in ${recordings.folder} (${formatFreeSpace(recordings.free_bytes)})`}
      </div>
      {config.error && (
        <div className="notice notice-error">Settings not applied: {config.error}</div>
      )}
      {config.waiting_for_reload && (
        <div className="muted">Waiting for MediaMTX to take the latest settings…</div>
      )}
      {service.port_conflicts.length > 0 && (
        <ul className="notice notice-error" data-testid="port-conflicts">
          {service.port_conflicts.map((conflict) => (
            <li key={`${conflict.protocol}-${conflict.port}`}>{conflict.message}</li>
          ))}
        </ul>
      )}
      {!service.answering && service.log_tail.length > 0 && (
        <details open>
          <summary>MediaMTX's last log lines</summary>
          <pre className="log-tail">{service.log_tail.join("\n")}</pre>
        </details>
      )}
    </section>
  );
}
