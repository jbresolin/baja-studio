/**
 * Presets of the selected camera: click to recall; long-press or Shift+click
 * to store the current position there; the pencil renames it.
 *
 * Slots 1–9 are always shown (they match the 1–9 keys and the controller's
 * preset buttons, so slot 1 is preset number 0), plus any named preset beyond.
 */

import { useRef, useState } from "react";

import { presetLabel, type CameraView } from "../../lib/cameras";

const LONG_PRESS_MS = 600;
const ALWAYS_SHOWN = 9;

interface PresetPanelProps {
  camera: CameraView;
  run: (command: string, args: Record<string, unknown>) => Promise<unknown>;
}

export function presetSlots(camera: CameraView): number[] {
  const max = camera.capabilities?.preset_max;
  const slots = new Set<number>();
  for (let n = 0; n < ALWAYS_SHOWN && (max === undefined || max === null || n <= max); n++) {
    slots.add(n);
  }
  for (const preset of camera.presets ?? []) slots.add(preset.n);
  return [...slots].sort((a, b) => a - b);
}

export function PresetPanel({ camera, run }: PresetPanelProps) {
  const [renaming, setRenaming] = useState<{ n: number; label: string } | null>(null);
  const pressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const longPressed = useRef(false);

  if (camera.capabilities && camera.capabilities.preset_max === null) {
    return <p className="muted">This camera has no presets.</p>;
  }

  function cancelPress() {
    if (pressTimer.current !== null) clearTimeout(pressTimer.current);
    pressTimer.current = null;
  }

  function saveName() {
    if (!renaming) return;
    void run("rename_preset", { n: renaming.n, label: renaming.label.trim() });
    setRenaming(null);
  }

  return (
    <div className="preset-panel">
      {presetSlots(camera).map((n) =>
        renaming?.n === n ? (
          <form
            key={n}
            className="preset-rename"
            onSubmit={(event) => {
              event.preventDefault();
              saveName();
            }}
          >
            <input
              aria-label={`Name of preset ${n + 1}`}
              autoFocus
              maxLength={60}
              value={renaming.label}
              onChange={(event) => setRenaming({ n, label: event.target.value })}
              onKeyDown={(event) => event.key === "Escape" && setRenaming(null)}
            />
            <button type="submit">Save</button>
          </form>
        ) : (
          <div key={n} className="preset">
            <button
              className="preset-button"
              title="Click: recall · long-press or Shift+click: store here"
              onPointerDown={() => {
                longPressed.current = false;
                pressTimer.current = setTimeout(() => {
                  longPressed.current = true;
                  void run("store_preset", { n });
                }, LONG_PRESS_MS);
              }}
              onPointerUp={cancelPress}
              onPointerLeave={cancelPress}
              onContextMenu={(event) => event.preventDefault()}
              onClick={(event) => {
                cancelPress();
                if (longPressed.current) return; // the long press already stored it
                void run(event.shiftKey ? "store_preset" : "recall_preset", { n });
              }}
            >
              <span className="muted">{n + 1}</span> {presetLabel(n, camera.presets ?? [])}
            </button>
            <button
              className="preset-edit"
              aria-label={`Rename preset ${n + 1}`}
              onClick={() =>
                setRenaming({
                  n,
                  label: camera.presets?.find((preset) => preset.n === n)?.label ?? "",
                })
              }
            >
              ✎
            </button>
          </div>
        ),
      )}
    </div>
  );
}
