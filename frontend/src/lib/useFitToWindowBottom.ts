/**
 * Keep a panel no taller than the space between its top and the bottom of
 * the window, so it scrolls on its own and the rest of the page stays put.
 *
 * Used by the playout library: a long library otherwise makes the whole page
 * scroll, and the program strip, the queue and Take scroll away with it.
 *
 * Only on wide screens (more than 900 px, where the playout columns sit side
 * by side). On a phone the columns are tabs, and one page scroll is easier
 * to use than a scroll inside a scroll.
 */

import { useLayoutEffect, type RefObject } from "react";

/** The same breakpoint as the playout columns in styles.css. */
const SIDE_BY_SIDE_COLUMNS = "(min-width: 901px)";
/** Never shrink the panel below this, even on a very short window. */
const SMALLEST_HEIGHT_PX = 240;
/** Space left under the panel, so it doesn't touch the window's edge. */
const BOTTOM_MARGIN_PX = 20;

export function useFitToWindowBottom(ref: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const panel = element;

    function fit() {
      const wide =
        typeof window.matchMedia !== "function" || window.matchMedia(SIDE_BY_SIDE_COLUMNS).matches;
      if (!wide) {
        panel.style.maxHeight = "";
        return;
      }
      // The panel's top measured from the top of the page (not of the window),
      // so the height is the same whether or not the page is scrolled.
      const topOnPage = panel.getBoundingClientRect().top + window.scrollY;
      const height = Math.max(
        SMALLEST_HEIGHT_PX,
        window.innerHeight - topOnPage - BOTTOM_MARGIN_PX,
      );
      panel.style.maxHeight = `${Math.round(height)}px`;
    }

    fit();
    window.addEventListener("resize", fit);
    // Things above the panel come and go (the media controls while a clip is
    // on air, error notices, the take prompt) and move it down or up.
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(fit) : null;
    observer?.observe(document.body);
    return () => {
      window.removeEventListener("resize", fit);
      observer?.disconnect();
    };
  }, [ref]);
}
