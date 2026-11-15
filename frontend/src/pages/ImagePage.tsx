/**
 * The Image page (/cameras/image, WP6a brief §3): every camera's image
 * settings side by side, so cameras can be made to look alike.
 *
 * One card per camera that has image settings (a USB camera on the studio
 * PC, or an agent camera whose agent lists image controls), in the Cameras
 * page's order. A change is applied at once and saved; the server puts the
 * saved settings back by itself when a camera is replugged, the service
 * restarts or an agent comes back. On a phone the cards stack, one per row.
 */

import { useCallback } from "react";
import { Link } from "react-router-dom";

import { ImageCard } from "../components/imaging/ImageCard";
import { orderedCameraIds, type CamerasState } from "../lib/cameras";
import { useModuleState, useSendCommand } from "../lib/stateHooks";

export function ImagePage() {
  const state = useModuleState<CamerasState>("cameras");
  const sendCommand = useSendCommand();
  const run = useCallback(
    (command: string, args: Record<string, unknown>) => sendCommand("cameras", command, args),
    [sendCommand],
  );

  const cards = state?.image ?? {};
  const ordered = orderedCameraIds(state);
  const ids = [
    ...ordered.filter((id) => id in cards),
    ...Object.keys(cards).filter((id) => !ordered.includes(id)),
  ];
  const labelOf = (id: string) => state?.cameras[id]?.label ?? id;
  const usb = state?.usb;

  return (
    <div className="image-page">
      <div className="page-header">
        <h1>Image settings</h1>
        <Link to="/cameras">Cameras</Link>
        <Link to="/cameras/config">Camera setup</Link>
      </div>
      {usb && usb.status === "unavailable" && (
        <div className="notice">USB cameras: {usb.message}</div>
      )}
      {ids.length === 0 ? (
        <p className="muted">
          No camera has image settings yet. Pick a camera's USB device on{" "}
          <Link to="/cameras/config">Camera setup</Link>, or add an agent camera whose agent lists
          image controls.
        </p>
      ) : (
        <div className="image-grid" data-testid="image-grid">
          {ids.map((id) => (
            <ImageCard
              key={id}
              cameraId={id}
              label={labelOf(id)}
              card={cards[id]!}
              others={ids
                .filter((other) => other !== id)
                .map((other) => ({ id: other, label: labelOf(other) }))}
              run={run}
            />
          ))}
        </div>
      )}
    </div>
  );
}
