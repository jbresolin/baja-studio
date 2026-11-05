/**
 * The Playout page (/playout): library, queue, and program/preview in three
 * columns (WP5 brief §6). At phone width the library and queue become tabs
 * and the program strip stays on top.
 *
 * Every action is a command to the `playout` module; the server keeps all
 * the state, so every browser sees the same queue and program. Keys work
 * anywhere on the page except while typing in a field (`?` lists them).
 */

import { useEffect, useRef, useState } from "react";

import { AiredExport } from "../components/playout/AiredExport";
import { KeyboardLegend } from "../components/playout/KeyboardLegend";
import { LibraryPanel } from "../components/playout/LibraryPanel";
import { ProgramPanel, ProgramStrip } from "../components/playout/ProgramPanel";
import { QueuePanel } from "../components/playout/QueuePanel";
import { StatusLine } from "../components/playout/StatusLine";
import {
  isEditableTarget,
  playoutKeyAction,
  type PlayoutKeyAction,
  type PlayoutState,
} from "../lib/playout";
import { useClientId, useModuleState, useSendCommand } from "../lib/stateHooks";

const NOTICE_SHOWN_MS = 3000;
const CONFIRM_SHOWN_MS = 3000;

type Tab = "library" | "queue";

export function PlayoutPage() {
  const state = useModuleState<PlayoutState>("playout");
  const sendCommand = useSendCommand();
  const clientId = useClientId();
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [selectedClips, setSelectedClips] = useState<Set<number>>(new Set());
  const [selectedEntries, setSelectedEntries] = useState<Set<number>>(new Set());
  const [legendOpen, setLegendOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("library");
  const [error, setError] = useState<string | null>(null);
  const [confirmAsked, setConfirmAsked] = useState(false);

  useEffect(() => {
    if (error === null) return;
    const timer = setTimeout(() => setError(null), NOTICE_SHOWN_MS);
    return () => clearTimeout(timer);
  }, [error]);

  useEffect(() => {
    if (!confirmAsked) return;
    const timer = setTimeout(() => setConfirmAsked(false), CONFIRM_SHOWN_MS);
    return () => clearTimeout(timer);
  }, [confirmAsked]);

  const run = async (command: string, args: Record<string, unknown> = {}) => {
    try {
      return await sendCommand("playout", command, args);
    } catch (failure) {
      setError((failure as Error).message);
      return undefined;
    }
  };

  const take = async () => {
    const result = (await run("take", { cue_if_needed: true })) as { status?: string } | undefined;
    setConfirmAsked(result?.status === "confirm");
  };

  const onKeyAction = (action: PlayoutKeyAction) => {
    if (action === "take") void take();
    else if (action === "skip") void run("skip");
    else if (action === "drop_next") void run("drop_next");
    else if (action === "return") void run("return_to_scene");
    else if (action === "replay_last") void run("replay_last");
    else if (action === "toggle_legend") setLegendOpen((open) => !open);
    else if (action === "focus_search") {
      setTab("library");
      searchRef.current?.focus();
    } else if (action === "remove_selected" && selectedEntries.size > 0) {
      void run("queue_remove", { entry_ids: [...selectedEntries] });
      setSelectedEntries(new Set());
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const action = playoutKeyAction({
        key: event.key,
        targetIsEditable: isEditableTarget(event.target),
        shiftKey: event.shiftKey,
        ctrlKey: event.ctrlKey,
        altKey: event.altKey,
        metaKey: event.metaKey,
      });
      if (action === null) return;
      event.preventDefault();
      onKeyAction(action);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  if (!state) return <p className="muted">Waiting for the studio service…</p>;

  const confirming = state.take_confirmation !== null || confirmAsked;

  return (
    <div className="playout-page">
      <ProgramStrip program={state.program} />
      <StatusLine state={state} clientId={clientId} />
      {confirming && (
        <div className="take-confirm" role="alert">
          Press Take again within 3 s
          {state.take_confirmation && (
            <span className="muted"> (asked by {state.take_confirmation.requested_by})</span>
          )}
        </div>
      )}
      <div className="playout-tabs" role="tablist">
        {(["library", "queue"] as Tab[]).map((name) => (
          <button
            key={name}
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "active" : ""}
            onClick={() => setTab(name)}
          >
            {name === "library" ? "Library" : `Queue (${state.queue.length})`}
          </button>
        ))}
      </div>
      <div className="playout-columns">
        <div className={tab === "library" ? "playout-column" : "playout-column phone-hidden"}>
          <LibraryPanel
            library={state.library}
            selected={selectedClips}
            onSelectedChange={setSelectedClips}
            searchRef={searchRef}
            run={run}
          />
        </div>
        <div className={tab === "queue" ? "playout-column" : "playout-column phone-hidden"}>
          <QueuePanel
            queue={state.queue}
            totalSeconds={state.queue_total_s}
            selected={selectedEntries}
            onSelectedChange={setSelectedEntries}
            run={run}
          />
        </div>
        <div className="playout-column">
          <ProgramPanel
            state={state}
            queueLength={state.queue.length}
            onTake={() => void take()}
            run={run}
          />
          <AiredExport marker={state.export.stream_start_marker} run={run} />
        </div>
      </div>
      {error && (
        <div className="toast toast-error" role="status">
          {error}
        </div>
      )}
      {legendOpen && <KeyboardLegend onClose={() => setLegendOpen(false)} />}
    </div>
  );
}
