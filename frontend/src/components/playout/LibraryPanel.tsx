/**
 * The clip library: groups (provas, then labelled folders, then "Unmatched"),
 * collapsible, searchable, filterable, and the playlists above them.
 *
 * Clips are picked with their checkbox and queued together with "Add to
 * queue", or saved as a playlist. A clip whose file is missing is listed (so
 * the operator knows it exists) but cannot be picked. Files in the receiver
 * root that the manifest does not know are "unmatched": grey, and hidden
 * with one toggle. Each clip opens to edit notes, its played flag and, for
 * non-pipeline clips, its label.
 */

import { useState, type RefObject } from "react";

import {
  clipDetails,
  clipThumbnailUrl,
  formatKnownDuration,
  groupLibrary,
  UNMATCHED_HINT,
  type LibraryFilter,
  type PlayoutClip,
  type Playlist,
} from "../../lib/playout";
import { PlaylistList } from "./PlaylistList";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface LibraryPanelProps {
  library: Record<string, PlayoutClip>;
  playlists: Playlist[];
  /** The banner text when ffprobe/ffmpeg are missing (durations then show "?"). */
  toolsProblem: string | null;
  selected: Set<number>;
  onSelectedChange: (selected: Set<number>) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  run: Run;
}

const FILTERS: LibraryFilter[] = ["all", "unplayed", "played", "missing"];

export function LibraryPanel({
  library,
  playlists,
  toolsProblem,
  selected,
  onSelectedChange,
  searchRef,
  run,
}: LibraryPanelProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<LibraryFilter>("all");
  const [hideUnmatched, setHideUnmatched] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [openClip, setOpenClip] = useState<number | null>(null);
  const groups = groupLibrary(library, search, filter, hideUnmatched);

  function toggle(clipId: number) {
    const next = new Set(selected);
    if (next.has(clipId)) next.delete(clipId);
    else next.add(clipId);
    onSelectedChange(next);
  }

  function toggleGroup(name: string) {
    const next = new Set(collapsed);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setCollapsed(next);
  }

  async function addSelected() {
    await run("queue_add", { clip_ids: [...selected] });
    onSelectedChange(new Set());
  }

  async function saveSelectionAsPlaylist() {
    const name = window.prompt("Name of the new playlist");
    if (!name) return;
    await run("playlist_create", { name, clip_ids: [...selected] });
    onSelectedChange(new Set());
  }

  return (
    <section className="playout-library" aria-label="Library">
      <div className="playout-toolbar">
        <input
          ref={searchRef}
          type="search"
          placeholder="Search (/)"
          aria-label="Search the library"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select
          aria-label="Filter"
          value={filter}
          onChange={(event) => setFilter(event.target.value as LibraryFilter)}
        >
          {FILTERS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <label className="inline" title={UNMATCHED_HINT}>
          <input
            type="checkbox"
            checked={hideUnmatched}
            onChange={(event) => setHideUnmatched(event.target.checked)}
          />
          hide unmatched
        </label>
      </div>
      <div className="playout-toolbar">
        <button disabled={selected.size === 0} onClick={() => void addSelected()}>
          Add to queue{selected.size > 0 ? ` (${selected.size})` : ""}
        </button>
        <button disabled={selected.size === 0} onClick={() => void saveSelectionAsPlaylist()}>
          Save as playlist
        </button>
      </div>
      <PlaylistList playlists={playlists} library={library} run={run} />
      {groups.length === 0 && <p className="muted">No clips match.</p>}
      {groups.map((group) => (
        <div
          className={`clip-group clip-group-${group.kind}`}
          key={group.name}
          data-testid={`group-${group.name}`}
        >
          <div className="clip-group-bar">
            <button
              className="clip-group-header"
              aria-expanded={!collapsed.has(group.name)}
              onClick={() => toggleGroup(group.name)}
              title={group.kind === "unmatched" ? UNMATCHED_HINT : undefined}
            >
              {collapsed.has(group.name) ? "▸" : "▾"} {group.name}{" "}
              <span className="muted">({group.clips.length})</span>
            </button>
            {group.kind === "manual" && (
              <button
                className="small"
                title="Make a playlist of this folder's clips"
                onClick={() => void run("playlist_create", { group: group.name })}
              >
                + playlist
              </button>
            )}
          </div>
          {!collapsed.has(group.name) && (
            <ul className="clip-list">
              {group.clips.map((clip) => (
                <li
                  key={clip.id}
                  className={[
                    "clip",
                    clip.missing ? "missing" : "",
                    clip.kind === "unmatched" ? "unmatched" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  data-testid={`clip-${clip.id}`}
                  title={clip.kind === "unmatched" ? UNMATCHED_HINT : undefined}
                >
                  <div className="clip-row">
                    <input
                      type="checkbox"
                      aria-label={`Select ${clip.title}`}
                      checked={selected.has(clip.id)}
                      disabled={!clip.queueable}
                      onChange={() => toggle(clip.id)}
                    />
                    {clip.has_thumbnail ? (
                      <img className="clip-thumb" src={clipThumbnailUrl(clip.id)} alt="" />
                    ) : (
                      <span className="clip-thumb" />
                    )}
                    <button
                      className="clip-title"
                      onClick={() => setOpenClip(openClip === clip.id ? null : clip.id)}
                    >
                      {clip.title}
                      {clipDetails(clip) && (
                        <span className="clip-details muted">{clipDetails(clip)}</span>
                      )}
                    </button>
                    <span className="clip-meta">
                      {clip.missing ? (
                        <span className="badge badge-offline">missing</span>
                      ) : (
                        <span
                          data-testid={`duration-${clip.id}`}
                          title={
                            clip.duration_s === null
                              ? (toolsProblem ?? clip.probe_error ?? "not measured yet")
                              : undefined
                          }
                        >
                          {formatKnownDuration(clip.duration_s)}
                        </span>
                      )}
                      {clip.kind === "unmatched" && (
                        <span className="badge badge-unmatched">unmatched</span>
                      )}
                      {clip.played_count > 0 && <span className="badge badge-ok">played</span>}
                    </span>
                  </div>
                  {openClip === clip.id && <ClipEditor key={clip.id} clip={clip} run={run} />}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </section>
  );
}

/** Notes, played flag and (non-pipeline clips) label of one clip. Saved on blur. */
function ClipEditor({ clip, run }: { clip: PlayoutClip; run: Run }) {
  const [notes, setNotes] = useState(clip.notes);
  const [label, setLabel] = useState(clip.label ?? "");
  return (
    <div className="clip-editor">
      {clip.source === "manual" && (
        <label>
          Label
          <input
            aria-label="Label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            onBlur={() => {
              if (label !== (clip.label ?? "")) void run("set_label", { clip_id: clip.id, label });
            }}
          />
        </label>
      )}
      <label>
        Notes
        <textarea
          aria-label="Notes"
          rows={2}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          onBlur={() => {
            if (notes !== clip.notes) void run("set_notes", { clip_id: clip.id, notes });
          }}
        />
      </label>
      <label className="inline">
        <input
          type="checkbox"
          checked={clip.played_count > 0}
          onChange={(event) =>
            void run("mark_played", { clip_id: clip.id, played: event.target.checked })
          }
        />
        Played {clip.played_count > 1 ? `(${clip.played_count}×)` : ""}
      </label>
      <div className="muted mono">{clip.relative_path}</div>
      {clip.probe_error && <div className="field-error">{clip.probe_error}</div>}
    </div>
  );
}
