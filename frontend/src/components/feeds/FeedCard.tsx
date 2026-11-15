/**
 * One feed on the Feeds page: live or waiting, what arrives, the URLs to type
 * into the sender, the URL OBS reads, Add to OBS, and edit / remove.
 */

import { useState } from "react";

import {
  describePicture,
  formatBitrate,
  formatSince,
  type FeedCardState,
  type FeedSettings,
} from "../../lib/feeds";
import { AddToObs } from "./AddToObs";
import { CopyField } from "./CopyField";
import { FeedForm } from "./FeedForm";

interface FeedCardProps {
  card: FeedCardState;
  /** The feed as saved (null while the settings load). */
  saved: FeedSettings | null;
  takenNames: string[];
  saving: boolean;
  onSave: (feed: FeedSettings) => void;
  onRemove: () => void;
}

const STATUS_BADGE = { live: "badge-ok", waiting: "badge-degraded", unknown: "badge-offline" };

export function FeedCard({ card, saved, takenNames, saving, onSave, onRemove }: FeedCardProps) {
  const [editing, setEditing] = useState(false);
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const since = formatSince(card.since);

  return (
    <section className="feed-card" data-testid={`feed-${card.name}`}>
      <div className="card-header">
        <span className="card-title">
          {card.label || card.name} <span className="muted">{card.name}</span>
        </span>
        <span className={`badge ${STATUS_BADGE[card.status]}`}>{card.status}</span>
      </div>
      <div className="muted" data-testid={`feed-${card.name}-details`}>
        {card.status === "live" &&
          [
            card.protocol,
            describePicture(card),
            formatBitrate(card.bitrate_kbps),
            `${card.readers} reading`,
            since && `since ${since}`,
          ]
            .filter(Boolean)
            .join(" · ")}
        {card.status === "waiting" && `waiting for the sender${since ? ` since ${since}` : ""}`}
        {card.status === "unknown" && "MediaMTX is not answering"}
        {card.record && " · recording to disk"}
      </div>

      <h3>Sender</h3>
      {card.has_password && (
        <p className="muted">Replace &lt;password&gt; with this feed's password in the sender.</p>
      )}
      {card.publish_urls.map((item) => (
        <CopyField
          key={`${item.protocol}-${item.address}`}
          label={`${item.protocol} URL for ${card.name} via ${item.address}`}
          value={item.url}
        />
      ))}

      <h3>OBS</h3>
      <CopyField label={`OBS URL for ${card.name}`} value={card.obs_url} />
      <AddToObs feedName={card.name} sourceName={card.obs_source_name} />

      <div className="feed-actions">
        {saved && !editing && (
          <button type="button" onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
        {saved && !confirmingRemove && (
          <button type="button" onClick={() => setConfirmingRemove(true)}>
            Remove
          </button>
        )}
        {confirmingRemove && (
          <>
            <span>Remove {card.name}? Its sender will be refused.</span>
            <button type="button" onClick={onRemove}>
              Yes, remove
            </button>
            <button type="button" onClick={() => setConfirmingRemove(false)}>
              Keep it
            </button>
          </>
        )}
      </div>
      {saved && editing && (
        <FeedForm
          initial={saved}
          takenNames={takenNames}
          submitLabel="Save"
          saving={saving}
          onSubmit={(feed) => {
            onSave(feed);
            setEditing(false);
          }}
          onCancel={() => setEditing(false)}
        />
      )}
    </section>
  );
}
