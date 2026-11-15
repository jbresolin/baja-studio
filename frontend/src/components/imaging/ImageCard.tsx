/**
 * One camera's image settings on the Image page: its name and status, a
 * preview slot, every control, and the card's buttons.
 *
 * - Lock: what the camera chose on auto, saved as manual, so it stops
 *   drifting during the race (`lock_image`).
 * - All auto: every control that has an automatic mode back on it.
 * - Reset: the device's own defaults (USB cameras only; agents give none).
 * - Copy settings from: another camera's values, for the controls both have,
 *   fitted to this camera's ranges (`copy_image`).
 *
 * The preview slot is a marked `div` (class `preview-slot`, `data-camera`);
 * WP6b's preview component goes into it.
 */

import { useState } from "react";

import { allAutoChanges, type ImageCardView, type ImageChange } from "../../lib/imaging";
import { HealthBadge } from "../HealthBadge";
import { ControlSlider } from "./ControlSlider";

interface ImageCardProps {
  cameraId: string;
  label: string;
  card: ImageCardView;
  /** Other cameras with image settings, for "Copy settings from". */
  others: { id: string; label: string }[];
  run: (command: string, args: Record<string, unknown>) => Promise<unknown>;
}

export function ImageCard({ cameraId, label, card, others, run }: ImageCardProps) {
  const [copyFrom, setCopyFrom] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const offline = card.status !== "ok";
  const controls = Object.entries(card.controls);

  async function act(command: string, args: Record<string, unknown>, done?: string) {
    setMessage(null);
    try {
      const result = (await run(command, args)) as {
        skipped?: string[];
        locked?: string[];
      };
      if (command === "lock_image") {
        setMessage(
          result.locked?.length
            ? `Locked: ${result.locked.join(", ")}`
            : "Nothing was on auto; nothing to lock",
        );
      } else if (command === "copy_image" && result.skipped?.length) {
        setMessage(`Copied; this camera has no ${result.skipped.join(", ")}`);
      } else if (done) {
        setMessage(done);
      }
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  const change = (name: string, value: ImageChange) =>
    void act("set_image", { camera: cameraId, controls: { [name]: value } });

  return (
    <section className="card image-card" aria-label={`Image ${label}`} id={`image-${cameraId}`}>
      <div className="card-header">
        <span className="card-title">{label}</span>
        <HealthBadge state={card.status} />
      </div>
      <div className="muted image-card-source">
        {card.source === "usb" ? `USB: ${card.device?.name ?? "?"}` : `agent ${card.agent ?? "?"}`}
      </div>
      {card.status_reason && <div className="card-message">{card.status_reason}</div>}
      <div className="preview-slot" data-camera={cameraId} />
      {controls.length === 0 && <p className="muted">No controls read yet.</p>}
      {controls.map(([name, control]) => (
        <ControlSlider
          key={name}
          cameraId={cameraId}
          name={name}
          control={control}
          disabled={offline}
          onChange={change}
        />
      ))}
      <div className="button-row">
        <button
          type="button"
          disabled={offline}
          onClick={() => void act("lock_image", { camera: cameraId })}
        >
          Lock
        </button>
        <button
          type="button"
          disabled={offline}
          onClick={() =>
            void act(
              "set_image",
              { camera: cameraId, controls: allAutoChanges(card.controls) },
              "All on auto",
            )
          }
        >
          All auto
        </button>
        {card.source === "usb" && (
          <button
            type="button"
            disabled={offline}
            onClick={() =>
              void act("reset_image", { camera: cameraId }, "Device defaults restored")
            }
          >
            Reset to device defaults
          </button>
        )}
      </div>
      {others.length > 0 && (
        <div className="button-row">
          <select
            aria-label={`Copy settings to ${label} from`}
            value={copyFrom}
            onChange={(event) => setCopyFrom(event.target.value)}
          >
            <option value="">Copy settings from…</option>
            {others.map((other) => (
              <option key={other.id} value={other.id}>
                {other.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={offline || !copyFrom}
            onClick={() => void act("copy_image", { from: copyFrom, to: cameraId }, "Copied")}
          >
            Copy
          </button>
        </div>
      )}
      {message && (
        <div className="card-message" role="status">
          {message}
        </div>
      )}
    </section>
  );
}
