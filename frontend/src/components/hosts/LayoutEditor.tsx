/**
 * Which cards the hosts screen shows, in what order (WP9 §2, §5): drag a
 * card (or ▲/▼), tick it to show it, and Push to force it to the top of
 * every hosts screen right now ("next up" as a clip ends). The messages
 * card is always shown, under the others, so it is not listed.
 */

import { useState } from "react";

import { movedLayout, toggledLayout, type HostsState } from "../../lib/hosts";

type Run = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

export function LayoutEditor({ state, run }: { state: HostsState; run: Run }) {
  const [dragged, setDragged] = useState<string | null>(null);
  const cards = state.layout.filter((card) => card.pinned === null);
  const pushedId = state.pushed_card?.id ?? null;
  const move = (cardId: string, toIndex: number) =>
    void run("set_card_layout", { cards: movedLayout(state.layout, cardId, toIndex) });

  return (
    <section aria-label="Cards">
      <div className="playout-toolbar">
        <h2>Cards</h2>
        {pushedId !== null && (
          <button onClick={() => void run("push_card", { card_id: null })}>Unpush</button>
        )}
      </div>
      <ol className="queue-list">
        {cards.map((card, index) => (
          <li
            key={card.id}
            className={card.id === pushedId ? "queue-entry selected" : "queue-entry"}
            data-testid={`layout-${card.id}`}
            draggable
            onDragStart={() => setDragged(card.id)}
            onDragOver={(event) => event.preventDefault()}
            onDragEnd={() => setDragged(null)}
            onDrop={(event) => {
              event.preventDefault();
              if (dragged !== null && dragged !== card.id) move(dragged, index);
              setDragged(null);
            }}
          >
            <span className="drag-handle" aria-hidden="true">
              ⠿
            </span>
            <label className="queue-title">
              <input
                type="checkbox"
                checked={card.visible}
                onChange={(event) =>
                  void run("set_card_layout", {
                    cards: toggledLayout(state.layout, card.id, event.target.checked),
                  })
                }
              />{" "}
              {card.title}
              {card.placeholder && <span className="muted"> (not available yet)</span>}
            </label>
            <span className="queue-buttons">
              <button
                aria-label={`Move ${card.title} up`}
                disabled={index === 0}
                onClick={() => move(card.id, index - 1)}
              >
                ▲
              </button>
              <button
                aria-label={`Move ${card.title} down`}
                disabled={index === cards.length - 1}
                onClick={() => move(card.id, index + 1)}
              >
                ▼
              </button>
              <button
                aria-label={`Push ${card.title}`}
                disabled={card.id === pushedId}
                onClick={() => void run("push_card", { card_id: card.id })}
              >
                Push
              </button>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
