/**
 * The play queue: drag an entry to reorder (or use ▲/▼ from the keyboard),
 * click to select, ✕ to remove. The first entry is what "Cue" loads next.
 */

import { useState } from "react";

import { formatDuration, type QueueEntry } from "../../lib/playout";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

interface QueuePanelProps {
  queue: QueueEntry[];
  totalSeconds: number;
  selected: Set<number>;
  onSelectedChange: (selected: Set<number>) => void;
  run: Run;
}

export function QueuePanel({
  queue,
  totalSeconds,
  selected,
  onSelectedChange,
  run,
}: QueuePanelProps) {
  const [dragged, setDragged] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);

  function toggle(entryId: number) {
    const next = new Set(selected);
    if (next.has(entryId)) next.delete(entryId);
    else next.add(entryId);
    onSelectedChange(next);
  }

  function move(entryId: number, toIndex: number) {
    void run("queue_move", { entry_id: entryId, to_index: toIndex });
  }

  return (
    <section className="playout-queue" aria-label="Queue">
      <div className="playout-toolbar">
        <span>
          {queue.length} in queue ·{" "}
          <span data-testid="queue-total">{formatDuration(totalSeconds)}</span>
        </span>
        <button disabled={queue.length === 0} onClick={() => void run("queue_clear")}>
          Clear
        </button>
      </div>
      {queue.length === 0 && (
        <p className="muted">The queue is empty. Add clips from the library.</p>
      )}
      <ol className="queue-list">
        {queue.map((entry, index) => (
          <li
            key={entry.entry_id}
            data-testid={`queue-entry-${entry.entry_id}`}
            className={[
              "queue-entry",
              selected.has(entry.entry_id) ? "selected" : "",
              dragged === entry.entry_id ? "dragging" : "",
              over === index && dragged !== null ? "drop-target" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            draggable
            onDragStart={(event) => {
              setDragged(entry.entry_id);
              event.dataTransfer?.setData("text/plain", String(entry.entry_id));
            }}
            onDragOver={(event) => {
              event.preventDefault();
              setOver(index);
            }}
            onDragEnd={() => {
              setDragged(null);
              setOver(null);
            }}
            onDrop={(event) => {
              event.preventDefault();
              if (dragged !== null && dragged !== entry.entry_id) move(dragged, index);
              setDragged(null);
              setOver(null);
            }}
            onClick={() => toggle(entry.entry_id)}
          >
            <span className="drag-handle" aria-hidden="true">
              ⠿
            </span>
            {index === 0 && <span className="badge badge-cue">next</span>}
            <span className={entry.missing ? "queue-title missing" : "queue-title"}>
              {entry.title}
            </span>
            <span className="muted">{formatDuration(entry.duration_s)}</span>
            <span className="queue-buttons" onClick={(event) => event.stopPropagation()}>
              <button
                aria-label={`Move ${entry.title} up`}
                disabled={index === 0}
                onClick={() => move(entry.entry_id, index - 1)}
              >
                ▲
              </button>
              <button
                aria-label={`Move ${entry.title} down`}
                disabled={index === queue.length - 1}
                onClick={() => move(entry.entry_id, index + 1)}
              >
                ▼
              </button>
              <button
                aria-label={`Remove ${entry.title}`}
                onClick={() => void run("queue_remove", { entry_ids: [entry.entry_id] })}
              >
                ✕
              </button>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
