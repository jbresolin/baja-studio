/**
 * One macro step list: each step's kind and fields, with ▲/▼ to reorder,
 * ✕ to remove, and a menu to add. Levels are edited in dB.
 */

import {
  AUX_BUSES,
  CHANNEL_IDS,
  channelLabel,
  dbToLevel,
  levelToDb,
  moveItem,
  newStep,
  POINT_BUSES,
  SEND_SOURCES,
  type ChannelConfig,
  type MacroStep,
  type SendPoint,
} from "../../../lib/audio";

const STEP_NAMES: Record<MacroStep["op"], string> = {
  store_levels: "Close (remember level, silence)",
  restore_levels: "Open (back to remembered level)",
  set_fader: "Set fader",
  set_aux: "Set send",
  set_mute: "Mute / unmute",
  recall_scene: "Load mixer snapshot",
};

interface Props {
  title: string;
  steps: MacroStep[];
  channels: Record<string, ChannelConfig>;
  onChange: (steps: MacroStep[]) => void;
}

function ChannelSelect(props: {
  label: string;
  value: string;
  choices: string[];
  channels: Record<string, ChannelConfig>;
  onChange: (value: string) => void;
}) {
  return (
    <select
      aria-label={props.label}
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
    >
      {props.choices.map((channel) => (
        <option key={channel} value={channel}>
          {channel === "main" ? "main mix" : channelLabel(props.channels, channel)}
        </option>
      ))}
    </select>
  );
}

function DbInput({
  label,
  level,
  onChange,
}: {
  label: string;
  level: number;
  onChange: (level: number) => void;
}) {
  return (
    <input
      type="number"
      aria-label={label}
      step={0.5}
      min={-90}
      max={10}
      value={level <= 0 ? -90 : Math.round(levelToDb(level) * 10) / 10}
      onChange={(event) => onChange(dbToLevel(Number(event.target.value)))}
    />
  );
}

function PointsEditor(props: {
  label: string;
  points: SendPoint[];
  channels: Record<string, ChannelConfig>;
  onChange: (points: SendPoint[]) => void;
}) {
  const { points, channels, onChange } = props;
  return (
    <span className="step-points">
      {points.map((point, index) => (
        <span key={index} className="step-point">
          <ChannelSelect
            label={`${props.label} point ${index + 1} channel`}
            value={point.channel}
            choices={point.bus === "main" ? CHANNEL_IDS : SEND_SOURCES}
            channels={channels}
            onChange={(channel) =>
              onChange(points.map((p, i) => (i === index ? { ...p, channel } : p)))
            }
          />
          <ChannelSelect
            label={`${props.label} point ${index + 1} bus`}
            value={point.bus}
            choices={SEND_SOURCES.includes(point.channel) ? POINT_BUSES : ["main"]}
            channels={channels}
            onChange={(bus) => onChange(points.map((p, i) => (i === index ? { ...p, bus } : p)))}
          />
          {points.length > 1 && (
            <button
              type="button"
              aria-label="remove point"
              onClick={() => onChange(points.filter((_, i) => i !== index))}
            >
              −
            </button>
          )}
        </span>
      ))}
      <button type="button" onClick={() => onChange([...points, { channel: "1", bus: "main" }])}>
        + point
      </button>
    </span>
  );
}

function StepFields({
  step,
  label,
  channels,
  onChange,
}: {
  step: MacroStep;
  label: string;
  channels: Record<string, ChannelConfig>;
  onChange: (step: MacroStep) => void;
}) {
  switch (step.op) {
    case "set_fader":
      return (
        <>
          <ChannelSelect
            label={`${label} channel`}
            value={step.channel}
            choices={CHANNEL_IDS}
            channels={channels}
            onChange={(channel) => onChange({ ...step, channel })}
          />
          <DbInput
            label={`${label} dB`}
            level={step.level}
            onChange={(level) => onChange({ ...step, level })}
          />
        </>
      );
    case "set_aux":
      return (
        <>
          <ChannelSelect
            label={`${label} channel`}
            value={step.channel}
            choices={SEND_SOURCES}
            channels={channels}
            onChange={(channel) => onChange({ ...step, channel })}
          />
          <ChannelSelect
            label={`${label} bus`}
            value={step.bus}
            choices={[...AUX_BUSES]}
            channels={channels}
            onChange={(bus) => onChange({ ...step, bus: bus as typeof step.bus })}
          />
          <DbInput
            label={`${label} dB`}
            level={step.level}
            onChange={(level) => onChange({ ...step, level })}
          />
        </>
      );
    case "set_mute":
      return (
        <>
          <ChannelSelect
            label={`${label} channel`}
            value={step.channel}
            choices={CHANNEL_IDS}
            channels={channels}
            onChange={(channel) => onChange({ ...step, channel })}
          />
          <select
            aria-label={`${label} mute`}
            value={step.muted ? "mute" : "unmute"}
            onChange={(e) => onChange({ ...step, muted: e.target.value === "mute" })}
          >
            <option value="mute">mute</option>
            <option value="unmute">unmute</option>
          </select>
        </>
      );
    case "store_levels":
    case "restore_levels":
      return (
        <PointsEditor
          label={label}
          points={step.points}
          channels={channels}
          onChange={(points) => onChange({ ...step, points })}
        />
      );
    case "recall_scene":
      return (
        <input
          type="number"
          aria-label={`${label} snapshot`}
          min={1}
          max={64}
          value={step.snapshot}
          onChange={(e) => onChange({ ...step, snapshot: Number(e.target.value) })}
        />
      );
  }
}

export function StepEditor({ title, steps, channels, onChange }: Props) {
  return (
    <div className="step-list">
      <h4>{title}</h4>
      {steps.length === 0 && <p className="muted">No steps.</p>}
      <ol>
        {steps.map((step, index) => {
          const label = `${title} step ${index + 1}`;
          return (
            <li key={index} className="step">
              <select
                aria-label={`${label} kind`}
                value={step.op}
                onChange={(event) =>
                  onChange(
                    steps.map((s, i) =>
                      i === index ? newStep(event.target.value as MacroStep["op"]) : s,
                    ),
                  )
                }
              >
                {Object.entries(STEP_NAMES).map(([op, name]) => (
                  <option key={op} value={op}>
                    {name}
                  </option>
                ))}
              </select>
              <StepFields
                step={step}
                label={label}
                channels={channels}
                onChange={(changed) => onChange(steps.map((s, i) => (i === index ? changed : s)))}
              />
              <span className="step-buttons">
                <button
                  type="button"
                  aria-label={`move ${label} up`}
                  onClick={() => onChange(moveItem(steps, index, index - 1))}
                >
                  ▲
                </button>
                <button
                  type="button"
                  aria-label={`move ${label} down`}
                  onClick={() => onChange(moveItem(steps, index, index + 1))}
                >
                  ▼
                </button>
                <button
                  type="button"
                  aria-label={`remove ${label}`}
                  onClick={() => onChange(steps.filter((_, i) => i !== index))}
                >
                  ✕
                </button>
              </span>
            </li>
          );
        })}
      </ol>
      <select
        aria-label={`add a step to ${title}`}
        value=""
        onChange={(event) => {
          if (event.target.value)
            onChange([...steps, newStep(event.target.value as MacroStep["op"])]);
        }}
      >
        <option value="">+ add a step…</option>
        {Object.entries(STEP_NAMES).map(([op, name]) => (
          <option key={op} value={op}>
            {name}
          </option>
        ))}
      </select>
    </div>
  );
}
