/**
 * The clip library: grouped by prova, collapsible, searchable, filterable.
 *
 * Clips are picked with their checkbox and queued together with "Add to
 * queue". A clip whose file is missing is listed (so the operator knows it
 * exists) but cannot be picked. Each clip opens to edit notes, its played
 * flag and, for manual clips, its label.
 */

import { useState, type RefObject } from "react";

import {
  clipThumbnailUrl,
  formatDuration,
  groupLibrary,
  type LibraryFilter,
  type PlayoutClip,
} from "../../lib/playout";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface LibraryPanelProps {
  library: Record<string, PlayoutClip>;
  selected: Set<number>;
  onSelectedChange: (selected: Set<number>) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  run: Run;
}

const FILTERS: LibraryFilter[] = ["all", "unplayed", "played", "missing"];

export function LibraryPanel({
  library,
  selected,
  onSelectedChange,
  searchRef,
  run,
}: LibraryPanelProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<LibraryFilter>("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [openClip, setOpenClip] = useState<number | null>(null);
  const groups = groupLibrary(library, search, filter);

  function toggle(clipId: number) {
    const next = new Set(selected);
    if (next.has(clipId)) next.delete(clipId);
    else next.add(clipId);
    onSelectedChange(next);
  }

  function toggleGroup(prova: string) {
    const next = new Set(collapsed);
    if (next.has(prova)) next.delete(prova);
    else next.add(prova);
    setCollapsed(next);
  }

  async function addSelected() {
    await run("queue_add", { clip_ids: [...selected] });
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
        <button disabled={selected.size === 0} onClick={() => void addSelected()}>
          Add to queue{selected.size > 0 ? ` (${selected.size})` : ""}
        </button>
      </div>
      {groups.length === 0 && <p className="muted">No clips match.</p>}
      {groups.map((group) => (
        <div className="clip-group" key={group.prova} data-testid={`group-${group.prova}`}>
          <button
            className="clip-group-header"
            aria-expanded={!collapsed.has(group.prova)}
            onClick={() => toggleGroup(group.prova)}
          >
            {collapsed.has(group.prova) ? "▸" : "▾"} {group.prova}{" "}
            <span className="muted">({group.clips.length})</span>
          </button>
          {!collapsed.has(group.prova) && (
            <ul className="clip-list">
              {group.clips.map((clip) => (
                <li
                  key={clip.id}
                  className={clip.missing ? "clip missing" : "clip"}
                  data-testid={`clip-${clip.id}`}
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
                    </button>
                    <span className="clip-meta">
                      {clip.missing ? (
                        <span className="badge badge-offline">missing</span>
                      ) : (
                        formatDuration(clip.duration_s)
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

/** Notes, played flag and (manual clips only) label of one clip. Saved on blur. */
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
