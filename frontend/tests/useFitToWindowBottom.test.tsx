/**
 * The hook that keeps the playout library no taller than the window, so it
 * scrolls on its own. These tests check the height it sets, on a wide screen
 * and on a phone-sized one.
 */

import { render } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useFitToWindowBottom } from "../src/lib/useFitToWindowBottom";

/** A panel using the hook, whose top is `topPx` below the top of the page. */
function Panel({ topPx }: { topPx: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useFitToWindowBottom(ref);
  return (
    <div
      data-testid="panel"
      ref={(element) => {
        ref.current = element;
        // The test browser doesn't lay pages out, so tell it where the panel is.
        if (element) element.getBoundingClientRect = () => ({ top: topPx }) as DOMRect;
      }}
    />
  );
}

/** Pretend the window is `heightPx` tall and `wide` (side-by-side columns) or not. */
function windowOf(heightPx: number, wide: boolean) {
  vi.stubGlobal("innerHeight", heightPx);
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: wide, media: query }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useFitToWindowBottom", () => {
  it("caps the panel at the space left down to the window's bottom", () => {
    // A 1000 px window, the panel starting 300 px down: 1000 − 300 − 20 px margin.
    windowOf(1000, true);
    const { getByTestId } = render(<Panel topPx={300} />);
    expect(getByTestId("panel").style.maxHeight).toBe("680px");
  });

  it("never makes the panel shorter than 240 px on a very short window", () => {
    windowOf(400, true);
    const { getByTestId } = render(<Panel topPx={300} />);
    expect(getByTestId("panel").style.maxHeight).toBe("240px");
  });

  it("leaves the height alone on a phone, where the whole page scrolls instead", () => {
    windowOf(800, false);
    const { getByTestId } = render(<Panel topPx={300} />);
    expect(getByTestId("panel").style.maxHeight).toBe("");
  });
});
