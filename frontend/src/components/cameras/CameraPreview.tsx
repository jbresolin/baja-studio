/**
 * A live thumbnail of a camera (about one picture a second), for any page
 * (ADR 0020). It reads the camera's state itself, so a page needs only:
 *
 *   <CameraPreview camera="finish-crop" width={320} overlay clickToCentre />
 *
 * - Any camera with an OBS source shows a still of that source.
 * - A crop camera (an OBS crop filter, or a laptop agent's crop camera)
 *   shows the whole frame with its window drawn on it, and the zoom ("1.6×").
 * - An agent camera that serves snapshots (contract 1.1: a camera node's)
 *   needs no OBS source: the agent's picture of its whole frame is shown.
 *   OBS can only send the window itself (a screenshot is taken after the
 *   filter), so the window's picture is placed where the window is, and
 *   the last windows seen around it stay, dimmed: a fixed camera's
 *   surroundings change little. A camera set up with a `preview_source`
 *   (an uncropped view) shows the real whole picture instead.
 * - `clickToCentre`: clicking moves a crop camera's window there
 *   (`centre_window`, through the arbiter like any move).
 *
 * Nothing is fetched while the preview is off screen, the tab is hidden,
 * or after it is unmounted (lib/previews.ts).
 */

import { useRef, useState, type CSSProperties, type MouseEvent } from "react";

import type { CamerasState, CameraView } from "../../lib/cameras";
import { usePreviewImages, type PreviewRect } from "../../lib/previews";
import { useModuleState, useSendCommand } from "../../lib/stateHooks";

export interface CameraPreviewProps {
  /** The camera's id in the registry. */
  camera: string;
  /** Width of the screenshot asked for, in pixels (default 320). */
  width?: number;
  /** Draw a crop camera's window on the picture (default true). */
  overlay?: boolean;
  /** Clicking moves a crop camera's window to the clicked point. */
  clickToCentre?: boolean;
}

const DEFAULT_WIDTH = 320;
const DEFAULT_ASPECT = 16 / 9;
const NOTICE_MS = 2500;

/** Where a rectangle (fractions of the frame) sits inside the preview box. */
function placed(rect: PreviewRect): CSSProperties {
  return {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.w * 100}%`,
    height: `${rect.h * 100}%`,
  };
}

/** The whole frame's width over height, from what is known about the camera. */
function frameAspect(
  view: CameraView | undefined,
  imageAspect: number | null,
  shows: "window" | "full" | null,
): number {
  const crop = view?.crop;
  if (crop?.source_width && crop.source_height) return crop.source_width / crop.source_height;
  // A picture of the whole frame (an agent's snapshot) has the frame's shape.
  if (shows === "full" && imageAspect) return imageAspect;
  // An agent crop camera: the picture is the window, whose shape is known in fractions.
  if (crop && imageAspect) return (imageAspect * crop.h) / crop.w;
  return imageAspect ?? DEFAULT_ASPECT;
}

/** "1.6×" for a magnification of 1.6. */
export function zoomLabel(magnification: number): string {
  return `${magnification.toFixed(1)}×`;
}

export function CameraPreview({
  camera,
  width = DEFAULT_WIDTH,
  overlay = true,
  clickToCentre = false,
}: CameraPreviewProps) {
  const state = useModuleState<CamerasState>("cameras");
  const sendCommand = useSendCommand();
  const view = state?.cameras?.[camera];
  const element = useRef<HTMLDivElement>(null);
  const [imageAspect, setImageAspect] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const hasSnapshots = view?.capabilities?.snapshot === true;
  const hasSource = Boolean(view?.obs_source) || hasSnapshots;
  const images = usePreviewImages({ camera, width, wanted: hasSource, element });
  const crop = view?.crop ?? null;
  const isCrop = view?.capabilities?.kind === "crop";
  const newest = images.frames.at(-1);

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    if (!clickToCentre || !isCrop) return;
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return;
    const x = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    const y = Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
    const args = { camera, x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000 };
    sendCommand("cameras", "centre_window", args).catch((error: Error) => {
      setNotice(error.message);
      setTimeout(() => setNotice(null), NOTICE_MS);
    });
  };

  let message: string | null = null;
  if (!view) message = "Unknown camera";
  else if (!hasSource) message = isCrop ? null : "No OBS source (Camera setup)";
  else if (images.error && !newest) message = images.error;
  else if (!newest) message = "Loading preview…";

  return (
    <div
      ref={element}
      className={`camera-preview${clickToCentre && isCrop ? " camera-preview-clickable" : ""}`}
      style={{ aspectRatio: String(frameAspect(view, imageAspect, images.shows)) }}
      onClick={onClick}
      data-testid={`preview-${camera}`}
      title={clickToCentre && isCrop ? "Click to centre the window there" : undefined}
    >
      {images.frames.map((frame) => {
        const live = frame === newest;
        const style = frame.window ? placed(frame.window) : { inset: 0 };
        return (
          <img
            key={frame.url}
            src={frame.url}
            alt={live ? `${view?.label ?? camera} preview` : ""}
            className={live ? "preview-image" : "preview-image preview-trail"}
            style={style}
            draggable={false}
            onLoad={
              live
                ? (event) => {
                    const image = event.currentTarget;
                    if (image.naturalWidth && image.naturalHeight)
                      setImageAspect(image.naturalWidth / image.naturalHeight);
                  }
                : undefined
            }
          />
        );
      })}
      {overlay && isCrop && crop && (
        <div className="preview-window" style={placed(crop)} data-testid="preview-window" />
      )}
      {isCrop && crop && <span className="preview-zoom">{zoomLabel(crop.magnification)}</span>}
      {(message || notice) && <div className="preview-message">{notice ?? message}</div>}
      {images.error && newest && <div className="preview-stale">{images.error}</div>}
    </div>
  );
}
