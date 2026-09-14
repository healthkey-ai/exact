// How a computed value gets edited.
//
// A row like "TNBC status" or "CRAB" carries no pencil, and that is correct:
// EXACT derives it from other values on every match, so a write to the row
// itself would be accepted and then undone. What the reader can change are
// the values it is derived FROM, and the payload names them — `subform_details`
// holds exactly the inputs the derivation reads.
//
// So this dialog is not a nicety on top of the row; it is the row's only
// route to being changed at all.

import { useEffect, useRef } from "react";

import { FieldEdit } from "./FieldEdit";
import { editabilityOf } from "./writable";
import type { RowEditing } from "./TrialDetailPage";
import type { SubformEntry, TrialDetailField } from "./types";

export interface SubformDialogProps {
  field: TrialDetailField;
  entries: SubformEntry[];
  editing: RowEditing;
  onClose: () => void;
}

/** Whether a subform is worth opening at all.
 *
 *  Not "does the row have entries": a dialog listing four values none of
 *  which can be written is a door onto a wall. The therapy groups are exactly
 *  that — EXACT derives `first_line_therapy` and its siblings too — so the
 *  affordance has to ask about the entries, not about the row.
 */
export function subformCanBeEdited(
  entries: SubformEntry[] | null | undefined,
  editing: RowEditing | undefined,
): boolean {
  if (!entries?.length || !editing) return false;
  return entries.some(
    (entry) =>
      !entry.upatientRecomputed &&
      editabilityOf(entry.upatientField, editing.fields).can === "edit",
  );
}

export function SubformDialog({ field, entries, editing, onClose }: SubformDialogProps) {
  const panel = useRef<HTMLDivElement>(null);
  const closer = useRef<HTMLButtonElement>(null);

  // `aria-modal` is a claim about behaviour, not a mechanism: it tells a
  // screen reader the rest of the page is inert and does nothing whatever to
  // the Tab key. Left at that, a keyboard reader tabs straight out of the
  // dialog into controls they cannot see, and lands back in the table with no
  // idea the dialog is still open behind them. So focus moves in on open,
  // cycles inside while it is open, and goes back to the button that opened
  // it on close.
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
        onClose();
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
  }, [onClose]);

  return (
    <div className="exact-subform__scrim" onClick={onClose}>
      <div
        ref={panel}
        className="exact-subform"
        role="dialog"
        aria-modal="true"
        aria-label={`${field.label}: the values it is computed from`}
        // The scrim closes on click; the panel must not, or every click
        // inside the dialog would shut it.
        onClick={(event) => event.stopPropagation()}
      >
        <div className="exact-subform__head">
          <h3 className="exact-subform__title">{field.label}</h3>
          <button
            type="button"
            ref={closer}
            className="exact-subform__close"
            onClick={onClose}
          >
            Close
          </button>
        </div>

        <p className="exact-subform__note">
          This value is worked out from the ones below. Change those and it
          follows.
        </p>

        <ul className="exact-subform__list">
          {entries.map((entry) => {
            const editable = entry.upatientRecomputed
              ? ({ can: "unknown" } as const)
              : editabilityOf(entry.upatientField, editing.fields);
            const attribute = entry.upatientField;
            const pending =
              attribute && attribute in editing.outstanding
                ? editing.outstanding[attribute]
                : undefined;
            const outstanding = attribute != null && attribute in editing.outstanding;
            const failed = Boolean(attribute && attribute in editing.failed);
            const shown = outstanding ? pending : entry.value;

            return (
              <li key={entry.name} className="exact-subform__row">
                <span className="exact-subform__label">{entry.label}</span>
                <span className="exact-subform__value">
                  {shown == null || shown === "" ? "—" : String(shown)}
                </span>
                {outstanding ? (
                  <span className="exact-elig__saving">Saving…</span>
                ) : null}
                {failed ? (
                  <span className="exact-elig__error" role="alert">
                    Couldn't save that.
                  </span>
                ) : null}
                {editable.can === "edit" ? (
                  <FieldEdit
                    field={editable.field}
                    label={entry.label}
                    entry={editable.entry}
                    control={editable.control}
                    value={
                      failed && attribute
                        ? editing.failed[attribute]
                        : outstanding
                          ? pending
                          : entry.value
                    }
                    onSave={(value) => editing.save(editable.field, value)}
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
