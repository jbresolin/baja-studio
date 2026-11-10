/**
 * The Audio page (/audio, WP4 brief §5): quick controls for the XR18.
 *
 * - The visible strips with fader, dB and mute, in their colours.
 * - A view picker: the main mix, or one aux bus ("sends on faders"): the
 *   faders then set each strip's send to that bus, with its link state.
 *   The view is this page's own; it does not change anyone else's.
 * - Which bus the M-VAVE's knobs turn (shared: it is the device's state).
 * - Macro buttons, lit while active, and scenes.
 *
 * Everything is a command to the `audio` module; all pages, the M-VAVE and
 * the D200s see the same state.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AudioStatus, useNow } from "../components/audio/AudioStatus";
import { ChannelStrip } from "../components/audio/ChannelStrip";
import { MacroPad } from "../components/audio/MacroPad";
import { AUX_BUSES, CHANNEL_IDS, channelLabel, SEND_SOURCES, type AudioState } from "../lib/audio";
import { useModuleState, useSendCommand } from "../lib/stateHooks";
import { useAudioSettings } from "../lib/useAudioSettings";

const ERROR_SHOWN_MS = 4000;

export function AudioPage() {
  const state = useModuleState<AudioState>("audio");
  const settings = useAudioSettings(state?.settings_version);
  const sendCommand = useSendCommand();
  const [view, setView] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const now = useNow();

  useEffect(() => {
    if (error === null) return;
    const timer = setTimeout(() => setError(null), ERROR_SHOWN_MS);
    return () => clearTimeout(timer);
  }, [error]);

  const run = async (command: string, args: Record<string, unknown> = {}) => {
    try {
      return await sendCommand("audio", command, args);
    } catch (failure) {
      setError((failure as Error).message);
      return undefined;
    }
  };

  const showBus = (bus: string | null) => {
    setView(bus);
    if (bus !== null) void run("request_sends", { bus });
  };

  const config = settings.saved;
  if (!state || !config) return <p className="muted">Waiting for the studio service…</p>;

  const offline = state.mixer.connection !== "ok";
  const visible = CHANNEL_IDS.filter((channel) => config.channels[channel]?.visible);
  const strips = view === null ? visible : visible.filter((c) => SEND_SOURCES.includes(c));

  return (
    <div className="audio-page">
      <div className="page-header">
        <h1>Audio</h1>
        <Link
          className="settings-gear"
          to="/audio/config"
          aria-label="Audio setup"
          title="Audio setup"
        >
          ⚙
        </Link>
      </div>
      <AudioStatus state={state} now={now} />
      {error && (
        <div className="toast toast-error" role="alert">
          {error}
        </div>
      )}

      <div className="bus-picker" role="tablist" aria-label="Faders show">
        <button
          type="button"
          role="tab"
          aria-selected={view === null}
          className={view === null ? "active" : ""}
          onClick={() => showBus(null)}
        >
          Main mix
        </button>
        {AUX_BUSES.map((bus) => (
          <button
            type="button"
            role="tab"
            key={bus}
            aria-selected={view === bus}
            className={view === bus ? "active" : ""}
            style={{ borderColor: config.channels[bus]?.color }}
            onClick={() => showBus(bus)}
          >
            {channelLabel(config.channels, bus)}
          </button>
        ))}
        <label className="knob-bus">
          M-VAVE knobs:{" "}
          <select
            aria-label="M-VAVE knobs turn"
            value={state.midi.selected_bus ?? ""}
            onChange={(event) => void run("select_bus", { bus: event.target.value || null })}
          >
            <option value="">no bus</option>
            {AUX_BUSES.map((bus) => (
              <option key={bus} value={bus}>
                {channelLabel(config.channels, bus)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {view !== null && (
        <p className="muted bus-view-note">
          Faders set each strip's send to {channelLabel(config.channels, view)}.
        </p>
      )}

      <div className="strip-row" data-testid="strips">
        {strips.length === 0 && (
          <p className="muted">No channels are shown; tick "visible" in the audio setup.</p>
        )}
        {strips.map((channel) => (
          <ChannelStrip
            key={channel}
            channel={channel}
            label={channelLabel(config.channels, channel)}
            color={config.channels[channel]?.color ?? "#5b7fff"}
            view={state.channels[channel]}
            bus={view}
            linkState={view === null ? undefined : (state.links[channel]?.[view] ?? "unlinked")}
            lastSource={state.last_source[`${channel}:${view ?? "main"}`]}
            disabled={offline}
            onLevel={(level, dragging) =>
              void run(view === null ? "set_fader" : "set_aux", {
                channel,
                ...(view === null ? {} : { bus: view }),
                level,
                ...(dragging ? { dragging: true } : {}),
              })
            }
            onMute={() => void run("set_mute", { channel })}
            onRelink={() => void run("set_link", { channel, bus: view, state: "linked" })}
          />
        ))}
      </div>

      <MacroPad
        macros={config.macros}
        active={state.macros}
        scenes={config.scenes}
        disabled={offline}
        onMacro={(macro) => void run("run_macro", { macro })}
        onScene={(index) => void run("recall_scene", { index })}
      />
    </div>
  );
}
