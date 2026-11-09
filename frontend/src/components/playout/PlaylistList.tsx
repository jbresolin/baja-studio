/**
 * Playlists at the top of the library: one row each, queued in one click
 * (shuffled first when its shuffle is on, for sponsor loops). "Edit" opens
 * renaming, deleting and the playlist's own on-air actions, which replace
 * the settings' for its clips.
 */

import { useState } from "react";

import {
  formatDuration,
  type OnAirActions,
  type PlayoutClip,
  type Playlist,
} from "../../lib/playout";
import { useAudioMacros } from "../../lib/playoutSettings";
import { OnAirActionsEditor } from "./OnAirActionsEditor";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface PlaylistListProps {
  playlists: Playlist[];
  library: Record<string, PlayoutClip>;
  run: Run;
}

export function PlaylistList({ playlists, library, run }: PlaylistListProps) {
  const [editing, setEditing] = useState<number | null>(null);
  if (playlists.length === 0) return null;
  return (
    <div className="playlists" aria-label="Playlists">
      <div className="clip-group-header">Playlists</div>
      <ul className="clip-list">
        {playlists.map((playlist) => {
          const clips = playlist.clip_ids.map((id) => library[String(id)]).filter(Boolean);
          const seconds = clips.reduce((total, clip) => total + (clip!.duration_s ?? 0), 0);
          const unknown = clips.filter((clip) => clip!.duration_s === null).length;
          return (
            <li key={playlist.id} className="playlist" data-testid={`playlist-${playlist.id}`}>
              <div className="clip-row">
                <span className="clip-title">
                  {playlist.name}
                  <span className="clip-details muted">
                    {playlist.clip_ids.length} clips · {formatDuration(seconds)}
                    {unknown > 0 ? " +?" : ""}
                    {playlist.on_air ? " · own on-air actions" : ""}
                  </span>
                </span>
                <label className="inline" title="Shuffle when queued">
                  <input
                    type="checkbox"
                    aria-label={`Shuffle ${playlist.name}`}
                    checked={playlist.shuffle}
                    onChange={(event) =>
                      void run("playlist_update", {
                        playlist_id: playlist.id,
                        shuffle: event.target.checked,
                      })
                    }
                  />
                  shuffle
                </label>
                <button onClick={() => void run("playlist_queue", { playlist_id: playlist.id })}>
                  Queue
                </button>
                <button
                  className="small"
                  aria-expanded={editing === playlist.id}
                  onClick={() => setEditing(editing === playlist.id ? null : playlist.id)}
                >
                  Edit
                </button>
              </div>
              {editing === playlist.id && (
                <PlaylistEditor playlist={playlist} run={run} onClose={() => setEditing(null)} />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const NO_ACTIONS: OnAirActions = { on_clip_start: [], on_clip_end: [] };

function PlaylistEditor({
  playlist,
  run,
  onClose,
}: {
  playlist: Playlist;
  run: Run;
  onClose: () => void;
}) {
  const macros = useAudioMacros();
  const [name, setName] = useState(playlist.name);
  const [override, setOverride] = useState<OnAirActions | null>(playlist.on_air);
  return (
    <div className="clip-editor">
      <label>
        Name
        <input aria-label="Playlist name" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="inline">
        <input
          type="checkbox"
          checked={override !== null}
          onChange={(event) => setOverride(event.target.checked ? NO_ACTIONS : null)}
        />
        Own on-air actions (instead of the settings&apos;)
      </label>
      {override !== null && (
        <OnAirActionsEditor value={override} macros={macros} onChange={setOverride} />
      )}
      <div className="button-row">
        <button
          onClick={() => {
            void run("playlist_update", { playlist_id: playlist.id, name, on_air: override });
            onClose();
          }}
        >
          Save
        </button>
        <button
          onClick={() => {
            if (window.confirm(`Delete the playlist "${playlist.name}"? Its clips stay.`)) {
              void run("playlist_delete", { playlist_id: playlist.id });
              onClose();
            }
          }}
        >
          Delete
        </button>
      </div>
    </div>
  );
}
