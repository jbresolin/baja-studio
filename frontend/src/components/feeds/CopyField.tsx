/**
 * A URL to copy: shown whole in a read-only box, with a Copy button.
 *
 * Browsers only offer the clipboard on secure pages (HTTPS, or localhost).
 * On the studio's plain-http LAN address it may be missing: the button then
 * selects the text instead, so a long press (phone) or Ctrl+C copies it.
 */

import { useRef, useState } from "react";

interface CopyFieldProps {
  label: string;
  value: string;
}

export function CopyField({ label, value }: CopyFieldProps) {
  const input = useRef<HTMLInputElement>(null);
  const [outcome, setOutcome] = useState<"" | "copied" | "selected">("");

  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error("no clipboard on this page");
      await navigator.clipboard.writeText(value);
      setOutcome("copied");
    } catch {
      input.current?.select();
      setOutcome("selected");
    }
  }

  return (
    <div className="copy-field">
      <input
        ref={input}
        aria-label={label}
        readOnly
        value={value}
        onFocus={(event) => event.target.select()}
      />
      <button type="button" onClick={() => void copy()}>
        {outcome === "copied" ? "Copied" : "Copy"}
      </button>
      {outcome === "selected" && <span className="muted">selected: copy it with Ctrl+C</span>}
    </div>
  );
}
