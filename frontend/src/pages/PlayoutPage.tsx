/**
 * The Playout page (/playout): library, queue, and program/preview in three
 * columns that share the window's width (WP5 brief §6, WP5b §2). At phone
 * width the library and queue become tabs and the program strip stays on top.
 *
 * Above the columns: one banner when ffprobe/ffmpeg are missing, and the
 * reason the last cue failed. Problems the server reports on its own (an
 * on-air action that failed, auto-play falling back) arrive as `notice` and
 * show as a toast on every page.
 *
 * Under the strip, the media controls for the clip on air (WP5c), locked
 * until held for two seconds, then a one-line "message hosts" box (WP9)
 * that sends to the hosts screen without leaving the page. The gear opens
 * Playout → Settings.
 *
 * Every action is a command to the `playout` module; the server keeps all
 * the state, so every browser sees the same queue and program. Keys work
 * anywhere on the page except while typing in a field (`?` lists them).
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { HostsSendBox } from "../components/hosts/HostsSendBox";
import { AiredExport } from "../components/playout/AiredExport";
import { KeyboardLegend } from "../components/playout/KeyboardLegend";
import { LibraryPanel } from "../components/playout/LibraryPanel";
import { MediaControls } from "../components/playout/MediaControls";
import { ProgramPanel, ProgramStrip } from "../components/playout/ProgramPanel";
import { QueuePanel } from "../components/playout/QueuePanel";
import { StatusLine } from "../components/playout/StatusLine";
import type { HostsMessage } from "../lib/hosts";
import {
  isEditableTarget,
  playoutKeyAction,
  transportArgs,
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
  // Media controls unlocked for this airing (the started_at of the clip on air).
  const [unlockedFor, setUnlockedFor] = useState<string | null>(null);
  // The id of the server's notice already seen (undefined until the first
  // state arrives), so each notice toasts once.
  const seenNotice = useRef<number | null | undefined>(undefined);
  const loaded = state !== undefined;
  const notice = state?.notice ?? null;
  const program = state?.program ?? null;
  // A clip leaving the air (or the next one taking over) locks the controls again.
  const transportEnabled = program !== null && unlockedFor === program.started_at;

  useEffect(() => {
    if (!loaded) return;
    const id = notice?.id ?? null;
    if (seenNotice.current === undefined) {
      // Opening the page: a notice from before is history, not news.
      seenNotice.current = id;
      return;
    }
    if (notice !== null && id !== seenNotice.current) {
      seenNotice.current = id;
      setError(notice.message);
    }
  }, [loaded, notice]);

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
    else if (
      action === "transport_toggle" ||
      action === "transport_back" ||
      action === "transport_forward" ||
      action === "frame_back" ||
      action === "frame_forward"
    )
      void run("transport", transportArgs(action));
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
        transportEnabled,
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
      <ProgramStrip state={state} />
      {state.program && (
        <MediaControls
          program={state.program}
          speedChange={state.speed_change}
          enabled={transportEnabled}
          onEnabledChange={(enabled) =>
            setUnlockedFor(enabled ? (state.program?.started_at ?? null) : null)
          }
          run={run}
        />
      )}
      <HostsSendBox onError={setError} />
      <div className="playout-status-row">
        <StatusLine state={state} clientId={clientId} />
        <Link
          className="settings-gear"
          to="/playout/settings"
          aria-label="Playout settings"
          title="Playout settings"
        >
          ⚙
        </Link>
      </div>
      {state.tools?.problem && (
        <div className="notice notice-error" role="alert" data-testid="tools-banner">
          {state.tools.problem} <Link to="/playout/settings">Playout settings</Link>
        </div>
      )}
      {state.cue_problem && (
        <div className="notice notice-error" role="alert" data-testid="cue-problem">
          Cue failed: {state.cue_problem}
        </div>
      )}
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
            playlists={state.playlists ?? []}
            toolsProblem={state.tools?.problem ?? null}
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
            unknownCount={state.queue_unknown ?? 0}
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
          <AiredExport
            marker={state.export.stream_start_marker}
            run={run}
            hostsMessages={() =>
              sendCommand("hosts", "export_messages").then(
                (result) => (result as { messages: HostsMessage[] }).messages,
                () => null,
              )
            }
          />
        </div>
      </div>
      {error && (
        <div className="toast toast-error" role="status">
          {error}
        </div>
      )}
      {legendOpen && (
        <KeyboardLegend transportEnabled={transportEnabled} onClose={() => setLegendOpen(false)} />
      )}
    </div>
  );
}
