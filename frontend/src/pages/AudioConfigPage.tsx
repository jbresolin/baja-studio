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
import type { AudioState } from "../lib/audio";
import { useClientId, useModuleState, useSendCommand } from "../lib/stateHooks";
import { useAudioSettings } from "../lib/useAudioSettings";

const SECTIONS = [
  ["devices", "Mixer & devices"],
  ["channels", "Channels"],
  ["mvave", "M-VAVE"],
  ["links", "Link matrix"],
  ["macros", "Macros"],
  ["scenes", "Scenes"],
  ["d200", "D200"],
] as const;

type Section = (typeof SECTIONS)[number][0];

export function AudioConfigPage() {
  const live = useModuleState<AudioState>("audio");
  const settings = useAudioSettings(live?.settings_version);
  const sendCommand = useSendCommand();
  const clientId = useClientId();
  const [section, setSection] = useState<Section>("devices");
  const [commandError, setCommandError] = useState<string | null>(null);
  const now = useNow();
  const { draft, notice } = settings;

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
        <span className={settings.dirty ? "status-message" : "muted"}>
          {settings.dirty ? "Unsaved changes" : "No unsaved changes"}
        </span>
        <button
          type="button"
          onClick={() => void settings.save()}
          disabled={!settings.dirty || settings.saving}
        >
          Save
        </button>
        <button
          type="button"
          onClick={settings.discard}
          disabled={!settings.dirty || settings.saving}
        >
          Discard
        </button>
      </div>
      {settings.newerVersion !== null && (
        <div className="notice notice-error" role="status">
          Someone else saved the audio setup (version {settings.newerVersion}). Your edits are kept;
          saving them will be refused until you load the latest.
          <button type="button" onClick={settings.loadLatest}>
            Load latest (drop my edits)
          </button>
        </div>
      )}
      {notice && (
        <div className={notice.kind === "saved" ? "notice" : "notice notice-error"} role="status">
          {notice.text}
          {notice.kind === "conflict" && (
            <button type="button" onClick={settings.loadLatest}>
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
      {section === "links" && <LinkMatrix draft={draft} live={live} run={run} />}
      {section === "macros" && (
        <MacroEditor draft={draft} live={live} edit={settings.edit} ask={ask} />
      )}
      {section === "scenes" && <SceneTable draft={draft} edit={settings.edit} />}
      {section === "d200" && <D200Card draft={draft} origin={window.location.origin} />}
    </div>
  );
}
