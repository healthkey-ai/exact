// The modal shell: a scrim, a panel, and the keyboard contract that makes
// "modal" true rather than merely claimed.
//
// Extracted from `SubformDialog`, which had the only copy. A second dialog
// written beside it would have been a second focus trap to keep correct, and
// the one thing every reader of these notices agrees on is that focus traps
// are where dialogs go wrong.
import { useEffect, useRef } from "react";

export interface DialogProps {
  /** Names the dialog to a screen reader. */
  label: string;
  /** Shown in the head. Omit for a dialog that titles itself. */
  title?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}

export function Dialog({ label, title, onClose, children }: DialogProps) {
  const panel = useRef<HTMLDivElement>(null);
  const closer = useRef<HTMLButtonElement>(null);
  // Read through a ref so the effect below can run ONCE. Keyed on `onClose`
  // it re-ran on every render — a parent hands over a new closure each time —
  // and each run put focus back on Close, yanking the caret out of whatever
  // the reader was typing in.
  const close = useRef(onClose);
  close.current = onClose;

  // `aria-modal` is a claim about behaviour, not a mechanism: it tells a
  // screen reader the rest of the page is inert and does nothing whatever to
  // the Tab key. Left at that, a keyboard reader tabs straight out of the
  // dialog into controls they cannot see. So focus moves in on open, cycles
  // inside while it is open, and goes back to the opener on close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    closer.current?.focus();

    const focusable = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((el) => !el.hasAttribute("disabled"));

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      // Also when focus has already escaped — a click on the scrim, a
      // programmatic move — because the next Tab is the reader's way back in.
      if (!panel.current?.contains(active as Node)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
        return;
      }
      if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="exact-subform__scrim" onClick={onClose}>
      <div
        ref={panel}
        className="exact-subform"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        // The scrim closes on click; the panel must not, or every click
        // inside the dialog would shut it.
        onClick={(event) => event.stopPropagation()}
      >
        <div className="exact-subform__head">
          {/* Only when there is one: an empty heading is a violation in its
              own right, and the prop is optional. */}
          {title ? <h3 className="exact-subform__title">{title}</h3> : <span />}
          <button type="button" ref={closer} className="exact-subform__close" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
