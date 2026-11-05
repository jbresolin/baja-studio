/**
 * Setup: the macros. Each has an id (used in the D200's URLs, so renaming
 * one breaks a deck button), a label and colour, toggle or trigger, and its
 * step lists. **Dry run** asks the server what the steps would change on
 * the mixer right now, without changing anything, unsaved edits included.
 */

import { useState } from "react";

import {
  mirrorSteps,
  newMacro,
  type AudioSettingsValues,
  type AudioState,
  type MacroConfig,
  type MacroStep,
} from "../../../lib/audio";
import { CheckField, TextField } from "../../config/fields";
import type { EditSettings } from "./DevicesSection";
import { StepEditor } from "./StepEditor";

interface DryRunChange {
  kind: string;
  text: string;
}

export interface MacroEditorProps {
  draft: AudioSettingsValues;
  live: AudioState | undefined;
  edit: EditSettings;
  ask: (command: string, args: Record<string, unknown>) => Promise<unknown>;
}

export function MacroEditor({ draft, live, edit, ask }: MacroEditorProps) {
  const [open, setOpen] = useState<number | null>(null);
  const [dryRun, setDryRun] = useState<{ on: DryRunChange[]; off: DryRunChange[] } | null>(null);
  const macros = draft.macros;

  const update = (index: number, change: Partial<MacroConfig>) =>
    edit((d) => ({
      ...d,
      macros: d.macros.map((macro, i) => (i === index ? { ...macro, ...change } : macro)),
    }));

  const runDry = async (macro: MacroConfig) => {
    const describe = async (steps: MacroStep[]) =>
      ((await ask("dry_run", { steps })) as { changes: DryRunChange[] }).changes;
    setDryRun({ on: await describe(macro.on_steps), off: await describe(macro.off_steps) });
  };

  return (
    <section className="config-form" data-testid="macro-editor">
      {macros.map((macro, index) => (
        <div key={index} className="macro-card" style={{ borderColor: macro.color }}>
          <div className="card-header">
            <span className="card-title">
              {macro.label || macro.id} <span className="muted mono">{macro.id}</span>
              {live?.macros[macro.id]?.active && <span className="badge badge-ok">active</span>}
            </span>
            <span className="button-row">
              <button
                type="button"
                onClick={() => {
                  setOpen(open === index ? null : index);
                  setDryRun(null);
                }}
              >
                {open === index ? "Close" : "Edit"}
              </button>
              <button
                type="button"
                onClick={() =>
                  edit((d) => ({ ...d, macros: d.macros.filter((_, i) => i !== index) }))
                }
              >
                Delete
              </button>
            </span>
          </div>
          {open === index && (
            <div className="macro-body">
              <div className="form-grid">
                <TextField
                  label="Id (in the D200's URLs)"
                  value={macro.id}
                  onChange={(id) => update(index, { id: id.replace(/[^A-Za-z0-9_-]/g, "") })}
                />
                <TextField
                  label="Label"
                  value={macro.label}
                  onChange={(label) => update(index, { label })}
                />
                <label>
                  Colour{" "}
                  <input
                    type="color"
                    aria-label="macro colour"
                    value={macro.color}
                    onChange={(event) => update(index, { color: event.target.value })}
                  />
                </label>
                <CheckField
                  label="Toggle (on/off, lit while active)"
                  checked={macro.mode === "toggle"}
                  onChange={(toggle) => update(index, { mode: toggle ? "toggle" : "trigger" })}
                />
              </div>
              <StepEditor
                title={macro.mode === "toggle" ? "Turning on" : "Steps"}
                steps={macro.on_steps}
                channels={draft.channels}
                onChange={(on_steps) => update(index, { on_steps })}
              />
              {macro.mode === "toggle" && (
                <>
                  <StepEditor
                    title="Turning off"
                    steps={macro.off_steps}
                    channels={draft.channels}
                    onChange={(off_steps) => update(index, { off_steps })}
                  />
                  <button
                    type="button"
                    onClick={() => update(index, { off_steps: mirrorSteps(macro.on_steps) })}
                  >
                    Off = undo on
                  </button>
                </>
              )}
              <div className="button-row">
                <button type="button" onClick={() => void runDry(macro)}>
                  Dry run
                </button>
              </div>
              {dryRun && (
                <div className="dry-run" data-testid="dry-run">
                  <strong>Turning on would:</strong>
                  <ul>
                    {dryRun.on.map((change, i) => (
                      <li key={i}>{change.text}</li>
                    ))}
                    {dryRun.on.length === 0 && <li className="muted">change nothing</li>}
                  </ul>
                  {macro.mode === "toggle" && (
                    <>
                      <strong>Turning off would:</strong>
                      <ul>
                        {dryRun.off.map((change, i) => (
                          <li key={i}>{change.text}</li>
                        ))}
                        {dryRun.off.length === 0 && <li className="muted">change nothing</li>}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      ))}
      <div className="button-row">
        <button
          type="button"
          onClick={() => {
            edit((d) => ({ ...d, macros: [...d.macros, newMacro(d.macros)] }));
            setOpen(macros.length);
          }}
        >
          Add macro
        </button>
      </div>
    </section>
  );
}
