/**
 * The audio setup page (/audio/config, WP4 brief §5).
 *
 * Mixer and devices, channel labels/colours/visibility, the M-VAVE mapping
 * with MIDI learn, macros with a step editor and dry run, scenes, and the
 * D200 reference card: all edited in a local draft with **explicit Save and
 * Discard** (lib/useAudioSettings.ts). Live state keeps arriving over the
 * WebSocket (mixer names, macro states, learn) but never touches the draft.
 *
 * The link matrix is the exception: it is live routing, applied on tap.
 *
 * "Restore defaults…" (WP9b) on Macros and on the Link matrix goes through
 * the draft too: the macros restore edits the draft, the link matrix one
 * waits beside it, and both show what changes. Save keeps them (settings
 * first, so a stale version is still refused), then the server logs them
 * under this page's client id; Discard drops them.
 *
 * Seats (WP9b): the hosts' microphones and in-ear buses, the production
 * bus and microphones, and how the hosts may change their own mixes.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import { AudioStatus, useNow } from "../components/audio/AudioStatus";
import { ChannelTable } from "../components/audio/config/ChannelTable";
import { DevicesSection } from "../components/audio/config/DevicesSection";
import { LinkMatrix } from "../components/audio/config/LinkMatrix";
import { MacroEditor } from "../components/audio/config/MacroEditor";
import { MvaveMapping } from "../components/audio/config/MvaveMapping";
import { D200Card, SceneTable } from "../components/audio/config/ScenesAndD200";
import {
  LinkRestoreList,
  MacroRestoreList,
  RestoreDefaultsButton,
} from "../components/audio/config/RestoreDefaults";
import { SeatsSection } from "../components/audio/config/SeatsSection";
import type { AudioState, LinkRestore, MacroConfig, MacroRestoreChange } from "../lib/audio";
import type { HostsState } from "../lib/hosts";
import { useClientId, useModuleState, useSendCommand } from "../lib/stateHooks";
import { useAudioSettings } from "../lib/useAudioSettings";

const SECTIONS = [
  ["devices", "Mixer & devices"],
  ["channels", "Channels"],
  ["mvave", "M-VAVE"],
  ["links", "Link matrix"],
  ["seats", "Seats"],
  ["macros", "Macros"],
  ["scenes", "Scenes"],
  ["d200", "D200"],
] as const;

type Section = (typeof SECTIONS)[number][0];

export function AudioConfigPage() {
  const live = useModuleState<AudioState>("audio");
  const hosts = useModuleState<HostsState>("hosts");
  const seatNames = Object.fromEntries(
    (hosts?.seats ?? []).map((seat) => [seat.id, seat.screen_name || `Seat ${seat.id}`]),
  );
  const settings = useAudioSettings(live?.settings_version);
  const sendCommand = useSendCommand();
  const clientId = useClientId();
  const [section, setSection] = useState<Section>("devices");
  const [commandError, setCommandError] = useState<string | null>(null);
  const [macroRestore, setMacroRestore] = useState<MacroRestoreChange[] | null>(null);
  const [linkRestore, setLinkRestore] = useState<LinkRestore | null>(null);
  const now = useNow();
  const { draft, notice } = settings;
  const dirty = settings.dirty || macroRestore !== null || linkRestore !== null;

  const ask = async (command: string, args: Record<string, unknown> = {}) => {
    setCommandError(null);
    try {
      return await sendCommand("audio", command, args);
    } catch (error) {
      setCommandError(`${command}: ${(error as Error).message}`);
      return undefined;
    }
  };
  const run = (command: string, args: Record<string, unknown> = {}) => void ask(command, args);

  const restoreMacros = async () => {
    if (!draft) return;
    const preview = (await ask("preview_defaults", { part: "macros", macros: draft.macros })) as
      { macros: MacroConfig[]; changes: MacroRestoreChange[] } | undefined;
    if (!preview) return;
    settings.edit((d) => ({ ...d, macros: preview.macros }));
    setMacroRestore(preview.changes);
  };
  const restoreLinks = async () => {
    const preview = (await ask("preview_defaults", { part: "links" })) as LinkRestore | undefined;
    if (preview) setLinkRestore(preview);
  };
  const save = async () => {
    if (settings.dirty) {
      const version = await settings.save();
      if (version === null) return; // refused: the restores stay pending
      const restored = macroRestore?.some(
        (change) => change.change === "replaced" || change.change === "added",
      );
      if (restored) {
        await ask("restore_defaults", { part: "macros", version, changes: macroRestore });
      }
    }
    setMacroRestore(null);
    if (linkRestore && linkRestore.changes.length > 0) {
      const restored = await ask("restore_defaults", {
        part: "links",
        expected: linkRestore.current,
      });
      if (!restored) return;
    }
    setLinkRestore(null);
  };
  const dropRestores = () => {
    setMacroRestore(null);
    setLinkRestore(null);
  };

  if (!draft) {
    return notice ? (
      <div className="notice notice-error">{notice.text}</div>
    ) : (
      <p className="muted">Loading…</p>
    );
  }

  return (
    <div className="config-page audio-config">
      <div className="page-header">
        <h1>Audio setup</h1>
        <Link to="/audio">Audio</Link>
        <span className="muted">settings version {settings.version}</span>
      </div>
      {live && <AudioStatus state={live} now={now} />}

      <div className="save-bar" data-testid="save-bar">
        <span className={dirty ? "status-message" : "muted"}>
          {dirty ? "Unsaved changes" : "No unsaved changes"}
        </span>
        <button type="button" onClick={() => void save()} disabled={!dirty || settings.saving}>
          Save
        </button>
        <button
          type="button"
          onClick={() => {
            settings.discard();
            dropRestores();
          }}
          disabled={!dirty || settings.saving}
        >
          Discard
        </button>
      </div>
      {settings.newerVersion !== null && (
        <div className="notice notice-error" role="status">
          Someone else saved the audio setup (version {settings.newerVersion}). Your edits are kept;
          saving them will be refused until you load the latest.
          <button
            type="button"
            onClick={() => {
              settings.loadLatest();
              dropRestores();
            }}
          >
            Load latest (drop my edits)
          </button>
        </div>
      )}
      {notice && (
        <div className={notice.kind === "saved" ? "notice" : "notice notice-error"} role="status">
          {notice.text}
          {notice.kind === "conflict" && (
            <button
              type="button"
              onClick={() => {
                settings.loadLatest();
                dropRestores();
              }}
            >
              Load latest (drop my edits)
            </button>
          )}
        </div>
      )}
      {commandError && (
        <div className="notice notice-error" role="alert">
          {commandError}
        </div>
      )}

      <nav className="playout-tabs" role="tablist" aria-label="Audio setup sections">
        {SECTIONS.map(([key, title]) => (
          <button
            type="button"
            role="tab"
            key={key}
            aria-selected={section === key}
            className={section === key ? "active" : ""}
            onClick={() => setSection(key)}
          >
            {title}
          </button>
        ))}
      </nav>

      {section === "devices" && <DevicesSection draft={draft} edit={settings.edit} />}
      {section === "channels" && <ChannelTable draft={draft} live={live} edit={settings.edit} />}
      {section === "mvave" && (
        <MvaveMapping
          draft={draft}
          live={live}
          clientSource={clientId ? `web:${clientId}` : null}
          edit={settings.edit}
          run={run}
        />
      )}
      {section === "links" && (
        <>
          <RestoreDefaultsButton onClick={() => void restoreLinks()} />
          {linkRestore && (
            <LinkRestoreList changes={linkRestore.changes} channels={draft.channels} />
          )}
          <LinkMatrix draft={draft} live={live} run={run} />
        </>
      )}
      {section === "seats" && (
        <SeatsSection draft={draft} edit={settings.edit} seatNames={seatNames} />
      )}
      {section === "macros" && (
        <>
          <RestoreDefaultsButton onClick={() => void restoreMacros()} />
          {macroRestore && <MacroRestoreList changes={macroRestore} />}
          <MacroEditor draft={draft} live={live} edit={settings.edit} ask={ask} />
        </>
      )}
      {section === "scenes" && <SceneTable draft={draft} edit={settings.edit} />}
      {section === "d200" && <D200Card draft={draft} origin={window.location.origin} />}
    </div>
  );
}
