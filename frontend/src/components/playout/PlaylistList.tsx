/**
 * Playlists at the top of the library: one row each, queued in one click
 * (shuffled first when its shuffle is on, for sponsor loops). "Edit" opens
 * the playlist without recreating it (WP5c §2): its clips (drag or ▲/▼ to
 * reorder, ✕ to remove, search the library to add), renaming, deleting,
 * and its own on-air actions, which replace the settings' for its clips.
 *
 * Each change to the clips is sent at once as `playlist_update {clip_ids}`,
 * so it is in the command log; the name and on-air actions save with Save.
 */

import { useState } from "react";

import {
  formatDuration,
  formatKnownDuration,
  groupLibrary,
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
                <PlaylistEditor
                  playlist={playlist}
                  library={library}
                  run={run}
                  onClose={() => setEditing(null)}
                />
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
  library,
  run,
  onClose,
}: {
  playlist: Playlist;
  library: Record<string, PlayoutClip>;
  run: Run;
  onClose: () => void;
}) {
  const macros = useAudioMacros();
  const [name, setName] = useState(playlist.name);
  const [override, setOverride] = useState<OnAirActions | null>(playlist.on_air);
  const setClips = (clip_ids: number[]) =>
    void run("playlist_update", { playlist_id: playlist.id, clip_ids });
  return (
    <div className="clip-editor">
      <PlaylistClips playlist={playlist} library={library} onChange={setClips} />
      <AddFromLibrary
        library={library}
        onAdd={(clipId) => setClips([...playlist.clip_ids, clipId])}
      />
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

/** The playlist's clips in play order: drag or ▲/▼ to reorder, ✕ to remove. */
function PlaylistClips({
  playlist,
  library,
  onChange,
}: {
  playlist: Playlist;
  library: Record<string, PlayoutClip>;
  onChange: (clipIds: number[]) => void;
}) {
  const [dragged, setDragged] = useState<number | null>(null);
  const ids = playlist.clip_ids;
  const move = (from: number, to: number) => {
    if (to < 0 || to >= ids.length || from === to) return;
    const next = [...ids];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved!);
    onChange(next);
  };
  return (
    <ol className="queue-list playlist-clips" aria-label={`Clips of ${playlist.name}`}>
      {ids.map((clipId, index) => {
        const clip = library[String(clipId)];
        const title = clip?.title ?? `clip ${clipId}`;
        return (
          // A clip may be in a playlist twice, so the position is part of the key.
          <li
            key={`${index}-${clipId}`}
            data-testid={`playlist-clip-${index}`}
            className={dragged === index ? "queue-entry dragging" : "queue-entry"}
            draggable
            onDragStart={(event) => {
              setDragged(index);
              event.dataTransfer?.setData("text/plain", String(index));
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragEnd={() => setDragged(null)}
            onDrop={(event) => {
              event.preventDefault();
              if (dragged !== null) move(dragged, index);
              setDragged(null);
            }}
          >
            <span className="drag-handle" aria-hidden="true">
              ⠿
            </span>
            <span className={clip?.missing ? "queue-title missing" : "queue-title"}>{title}</span>
            <span className="muted">{formatKnownDuration(clip?.duration_s)}</span>
            <span className="queue-buttons">
              <button
                aria-label={`Move ${title} up in the playlist`}
                disabled={index === 0}
                onClick={() => move(index, index - 1)}
              >
                ▲
              </button>
              <button
                aria-label={`Move ${title} down in the playlist`}
                disabled={index === ids.length - 1}
                onClick={() => move(index, index + 1)}
              >
                ▼
              </button>
              <button
                aria-label={`Remove ${title} from the playlist`}
                // A playlist keeps at least one clip; delete the playlist instead.
                disabled={ids.length === 1}
                onClick={() => onChange(ids.filter((_, position) => position !== index))}
              >
                ✕
              </button>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

const ADD_RESULTS_SHOWN = 8;

/** Search the library and add a clip to the end of the playlist. */
function AddFromLibrary({
  library,
  onAdd,
}: {
  library: Record<string, PlayoutClip>;
  onAdd: (clipId: number) => void;
}) {
  const [search, setSearch] = useState("");
  const found =
    search.trim() === ""
      ? []
      : groupLibrary(library, search, "all")
          .flatMap((group) => group.clips)
          .filter((clip) => clip.queueable)
          .slice(0, ADD_RESULTS_SHOWN);
  return (
    <div className="playlist-add">
      <input
        type="search"
        aria-label="Add a clip to the playlist"
        placeholder="Add a clip: search the library"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {found.length > 0 && (
        <ul className="clip-list">
          {found.map((clip) => (
            <li key={clip.id} className="clip-row">
              <span className="clip-title">
                {clip.title}
                <span className="clip-details muted">{clip.group}</span>
              </span>
              <button
                className="small"
                aria-label={`Add ${clip.title} to the playlist`}
                onClick={() => onAdd(clip.id)}
              >
                Add
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
