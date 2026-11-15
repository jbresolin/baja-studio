/**
 * Camera previews (ADR 0020): fetch `/api/cameras/preview/<camera>` about once
 * a second while a preview is on screen, and keep what came back.
 *
 * Fetching stops when the preview is unmounted, scrolled out of view, or the
 * tab is hidden: the server takes a screenshot only when a page asks, so a
 * page nobody looks at must not ask. A new request goes out only after the
 * previous answer arrived (so a slow OBS slows the page, it is not flooded).
 *
 * A crop camera's screenshot is its window, not the whole picture. The answer
 * says where the window was (`X-Crop-Window`), and the hook keeps the last few
 * windows it saw (`TRAIL_LENGTH`) so the page can piece the frame together:
 * what is around the window now is what was seen there last.
 */

import { useEffect, useRef, useState, type RefObject } from "react";

export const PREVIEW_INTERVAL_MS = 1000;
/** How many earlier windows a crop camera's preview keeps around the current one. */
export const TRAIL_LENGTH = 12;
/** Two windows this close (fractions of the frame) are the same place: the newer replaces the older. */
const SAME_PLACE = 0.02;

export interface PreviewRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** One image the server sent, and where it belongs on the frame (null: the whole frame). */
export interface PreviewFrame {
  url: string;
  window: PreviewRect | null;
  seq: number;
}

export interface PreviewImages {
  /** Oldest first; the last one is the newest. */
  frames: PreviewFrame[];
  /** "window": the frames are the crop window; "full": the frame is the whole picture. */
  shows: "window" | "full" | null;
  /** Why there is no picture (the server's words, e.g. "OBS offline: ..."), or null. */
  error: string | null;
}

/** Read "x,y,w,h" from the `X-Crop-Window` header. */
export function parseWindowHeader(value: string | null): PreviewRect | null {
  if (!value) return null;
  const numbers = value.split(",").map(Number);
  if (numbers.length !== 4 || numbers.some((n) => !Number.isFinite(n))) return null;
  const [x, y, w, h] = numbers as [number, number, number, number];
  return { x, y, w, h };
}

/** True when two windows are at the same place and of the same size. */
function samePlace(a: PreviewRect | null, b: PreviewRect | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    Math.abs(a.x - b.x) < SAME_PLACE &&
    Math.abs(a.y - b.y) < SAME_PLACE &&
    Math.abs(a.w - b.w) < SAME_PLACE &&
    Math.abs(a.h - b.h) < SAME_PLACE
  );
}

/** Add a frame to the trail: drop frames at the same place, keep at most TRAIL_LENGTH. */
export function addToTrail(
  frames: PreviewFrame[],
  frame: PreviewFrame,
  keepTrail: boolean,
): { kept: PreviewFrame[]; dropped: PreviewFrame[] } {
  const candidates = keepTrail ? frames : [];
  const kept = candidates.filter((old) => !samePlace(old.window, frame.window));
  kept.push(frame);
  const overflow = Math.max(0, kept.length - TRAIL_LENGTH);
  const trimmed = kept.slice(overflow);
  const dropped = frames.filter((old) => !trimmed.includes(old));
  return { kept: trimmed, dropped };
}

/** True while the element is on screen (always, where the browser cannot tell). */
function useOnScreen(element: RefObject<HTMLElement | null>): boolean {
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const target = element.current;
    if (!target || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) setOnScreen(entry.isIntersecting);
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, [element]);
  return onScreen;
}

/** True while the browser tab is visible. */
function useTabVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

interface PreviewOptions {
  camera: string;
  width: number;
  /** False when there is nothing to fetch (no OBS source): no request at all. */
  wanted: boolean;
  element: RefObject<HTMLElement | null>;
  intervalMs?: number;
}

/** Fetch a camera's preview about once a second while it is wanted, on screen and visible. */
export function usePreviewImages({
  camera,
  width,
  wanted,
  element,
  intervalMs = PREVIEW_INTERVAL_MS,
}: PreviewOptions): PreviewImages {
  const onScreen = useOnScreen(element);
  const tabVisible = useTabVisible();
  const active = wanted && onScreen && tabVisible;
  const [images, setImages] = useState<PreviewImages>({ frames: [], shows: null, error: null });
  const framesRef = useRef<PreviewFrame[]>([]);

  // A different camera starts afresh: its old pictures belong to another frame.
  useEffect(() => {
    return () => {
      for (const frame of framesRef.current) URL.revokeObjectURL(frame.url);
      framesRef.current = [];
      setImages({ frames: [], shows: null, error: null });
    };
  }, [camera]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let seq = 0;

    const fetchOnce = async () => {
      const url = `/api/cameras/preview/${encodeURIComponent(camera)}?width=${width}`;
      try {
        const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) {
          const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
          const detail = typeof body.detail === "string" ? body.detail : response.statusText;
          setImages((current) => ({ ...current, error: detail || "no preview" }));
        } else {
          const blob = await response.blob();
          if (controller.signal.aborted) return;
          const shows = response.headers.get("X-Preview-Shows") === "window" ? "window" : "full";
          seq += 1;
          const frame: PreviewFrame = {
            url: URL.createObjectURL(blob),
            window:
              shows === "window" ? parseWindowHeader(response.headers.get("X-Crop-Window")) : null,
            seq,
          };
          const { kept, dropped } = addToTrail(framesRef.current, frame, shows === "window");
          for (const old of dropped) URL.revokeObjectURL(old.url);
          framesRef.current = kept;
          setImages({ frames: kept, shows, error: null });
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        setImages((current) => ({ ...current, error: (error as Error).message }));
      }
      if (!controller.signal.aborted) timer = setTimeout(() => void fetchOnce(), intervalMs);
    };

    void fetchOnce();
    return () => {
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [active, camera, width, intervalMs]);

  return images;
}
