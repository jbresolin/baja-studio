/**
 * Macro buttons (lit while active, worked out from the mixer) and scenes.
 * A scene that loads a mixer snapshot asks first: it replaces the whole mix.
 */

import { useState } from "react";

import type { MacroConfig, SceneConfig } from "../../lib/audio";

export interface MacroPadProps {
  macros: MacroConfig[];
  active: Record<string, { active: boolean }>;
  scenes: SceneConfig[];
  disabled: boolean;
  onMacro: (id: string) => void;
  onScene: (index: number) => void;
}

export function MacroPad({ macros, active, scenes, disabled, onMacro, onScene }: MacroPadProps) {
  const [confirming, setConfirming] = useState<SceneConfig | null>(null);

  const askScene = (scene: SceneConfig) => {
    if (scene.snapshot !== null) setConfirming(scene);
    else onScene(scene.index);
  };

  return (
    <div className="macro-pad">
      <div className="macro-grid" data-testid="macro-grid">
        {macros.map((macro) => {
          const on = active[macro.id]?.active === true;
          return (
            <button
              type="button"
              key={macro.id}
              className={on ? "macro-button active" : "macro-button"}
              style={{ borderColor: macro.color, background: on ? macro.color : undefined }}
              aria-pressed={macro.mode === "toggle" ? on : undefined}
              disabled={disabled}
              onClick={() => onMacro(macro.id)}
            >
              {macro.label || macro.id}
            </button>
          );
        })}
      </div>
      {scenes.length > 0 && (
        <div className="scene-row">
          <span className="muted">Scenes</span>
          {scenes.map((scene) => (
            <button
              type="button"
              key={scene.index}
              disabled={disabled}
              onClick={() => askScene(scene)}
            >
              {scene.index}. {scene.label || `Scene ${scene.index}`}
            </button>
          ))}
        </div>
      )}
      {confirming && (
        <div className="notice notice-error" role="alertdialog">
          Recall “{confirming.label || `Scene ${confirming.index}`}”? It loads mixer snapshot{" "}
          {confirming.snapshot}, which changes everything on the mixer (levels, mutes, sends).
          <div className="button-row">
            <button
              type="button"
              onClick={() => {
                onScene(confirming.index);
                setConfirming(null);
              }}
            >
              Recall
            </button>
            <button type="button" onClick={() => setConfirming(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
