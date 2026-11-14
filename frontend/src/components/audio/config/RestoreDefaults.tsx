/**
 * "Restore defaults…" for the macros and the link matrix (WP9b): a button,
 * and once pressed, what the restore changes. Nothing reaches the setup or
 * the mixer until Save; Discard drops it (AudioConfigPage.tsx).
 */

import {
  channelLabel,
  type ChannelConfig,
  type LinkRestoreChange,
  type MacroRestoreChange,
} from "../../../lib/audio";

const MACRO_CHANGES: Record<MacroRestoreChange["change"], string> = {
  replaced: "replaced by the default",
  added: "added",
  unchanged: "unchanged",
  custom: "kept (not a default)",
};

export function RestoreDefaultsButton({ onClick }: { onClick: () => void }) {
  return (
    <p className="button-row">
      <button type="button" onClick={onClick}>
        Restore defaults…
      </button>
    </p>
  );
}

export function MacroRestoreList({ changes }: { changes: MacroRestoreChange[] }) {
  return (
    <div className="notice" role="status" data-testid="macro-restore">
      <strong>Restore default macros</strong>: Save to keep, Discard to drop.
      <ul>
        {changes.map((change) => (
          <li key={change.id} className={`restore-${change.change}`}>
            {change.label || change.id} <span className="muted mono">{change.id}</span>:{" "}
            {MACRO_CHANGES[change.change]}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function LinkRestoreList({
  changes,
  channels,
}: {
  changes: LinkRestoreChange[];
  channels: Record<string, ChannelConfig>;
}) {
  return (
    <div className="notice" role="status" data-testid="link-restore">
      <strong>Restore the default link matrix</strong>: Save to apply, Discard to drop.
      {changes.length === 0 ? (
        <p>The matrix is already the default.</p>
      ) : (
        <ul>
          {changes.map((change) => (
            <li key={`${change.channel}:${change.bus}`}>
              {channelLabel(channels, change.channel)} → {channelLabel(channels, change.bus)}:{" "}
              {change.before} → {change.after}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Held levels the restore lets go: sends an old macro left closed (a closed
 * send ignores its fader). Shown only when there are some.
 */
export function ReleasedList({
  points,
  channels,
}: {
  points: string[];
  channels: Record<string, ChannelConfig>;
}) {
  if (points.length === 0) return null;
  return (
    <div className="notice" role="status" data-testid="released-levels">
      <strong>Held sends released</strong> on Save: they follow their fader again.
      <ul>
        {points.map((point) => {
          const [channel, bus] = point.split(":") as [string, string];
          return (
            <li key={point}>
              {channelLabel(channels, channel)} → {channelLabel(channels, bus)}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
