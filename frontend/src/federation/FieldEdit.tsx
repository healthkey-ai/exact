// The control that writes one eligibility value.
//
// It is drawn only where PROMOP has said, for this caller and this patient,
// that the attribute can be written — see `writable.ts`. Everywhere else the
// row stays exactly as it reads today: a pencil that opens a box saving into
// nothing is worse than no pencil, and a reader cannot tell the difference
// until after they have typed.
//
// Save does not wait. The value is handed to the queue, the editor closes,
// and the row shows it on trust until the record answers — what came back is
// the row's business, not the editor's, because by then the editor is gone.
// See `patientWriter.ts`.

import { useEffect, useRef, useState } from "react";

import { optionsOf, splitJoined } from "./writable";
import type { EditControl, WritableFieldEntry } from "./writable";

export interface FieldEditProps {
  /** Canonical patient attribute, as PROMOP names it. */
  field: string;
  /** The row's label, for the control's accessible name. */
  label: string;
  entry: WritableFieldEntry;
  control: EditControl;
  /** The patient's current value, as the detail response reports it. */
  value: unknown;
  /** Unit to show beside a number. The row's own unit wins over the
   *  descriptor's: it is the one the reader is looking at. */
  units?: string;
  /** Queue the value. Returns at once: the reader has decided, and the row
   *  shows their value on trust while the request goes out with whatever else
   *  they change in the same breath. What came back is the row's business,
   *  not the editor's — by the time an answer arrives the editor is closed,
   *  which is the whole point of a queue. */
  onSave: (value: unknown) => void;
  /** Told to the row, which hides the value, its unit and the mismatch mark
   *  while the editor stands in their place. Side by side they compete for a
   *  column that can be a few characters wide, and the reader is shown the
   *  old value next to the box they are changing it in. */
  onOpenChange?: (open: boolean) => void;
}

const PencilIcon = () => (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M12 20h9" />
    <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
  </svg>
);

/** The draft a control starts with.
 *
 *  Lists and scalars are kept apart rather than joined into a string: joining
 *  them would make "A, B" indistinguishable from a single value that happens
 *  to contain a comma, and the multiselect writes a list back. */
/** What a date box will accept.
 *
 *  `<input type="date">` wants `YYYY-MM-DD` and `datetime-local` wants
 *  `YYYY-MM-DDTHH:mm`; anything else — an ISO string with a `Z`, an offset, or
 *  seconds — is rejected and the box renders EMPTY. That matters more than it
 *  sounds: an empty box saved sends `null`, so a value the reader never
 *  touched would erase the stored one. The same shape as the `type="number"`
 *  problem, and it erases just as quietly.
 *
 *  Returns "" for anything unrecognisable, which shows an empty box — but an
 *  empty box the reader can see is honest, where one that looks like a
 *  cleared value is not. */
function dateDraft(value: unknown, control: EditControl): string {
  if (value == null || value === "") return "";
  const text = String(value);
  const match = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(text);
  if (!match) return "";
  if (control === "date") return match[1];
  return match[2] ? `${match[1]}T${match[2]}` : `${match[1]}T00:00`;
}

function draftFrom(value: unknown, control: EditControl): string | string[] {
  if (control === "date" || control === "datetime") return dateDraft(value, control);
  // Comma-joined, not a list: see `splitJoined`. Wrapping the raw string in
  // an array instead would select nothing and offer one option that is not on
  // the list.
  if (control === "multiselect") return splitJoined(value);
  if (control === "boolean") {
    if (value === true) return "true";
    if (value === false) return "false";
    return "";
  }
  if (value == null) return "";
  if (Array.isArray(value)) return value.length ? String(value[0]) : "";
  return String(value);
}

/** What goes on the wire.
 *
 *  An emptied box sends `null` rather than `""`: the record's columns are
 *  nullable and "" would be a value for many of them. Numbers are sent as
 *  numbers so the server is not asked to parse, and a number box holding
 *  something unparseable is refused here rather than sent — the serializer
 *  would answer 400 and the reader would have learned nothing they could not
 *  have been told immediately. */
function payloadFrom(draft: string | string[], control: EditControl): unknown {
  if (Array.isArray(draft)) return draft;
  const text = draft.trim();
  if (text === "") return null;
  if (control === "boolean") return text === "true";
  // A local datetime carries no zone, and the server stores instants. Sent as
  // written plus seconds, so it round-trips as the reader typed it rather than
  // being shifted by whatever zone the browser happens to be in.
  if (control === "datetime") return text.length === 16 ? `${text}:00` : text;
  if (control === "number") {
    // Decimal only. `Number` also accepts "0x10" (→ 16), "0b11" and "1e5",
    // none of which anyone types into a lab value on purpose, and all of
    // which would be saved as a number nobody meant.
    if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(text)) return NaN;
    return Number(text);
  }
  return text;
}

/** A `datetime` gets a datetime-local box, not a date one: dropping the time
 *  would rewrite the stored value the moment an unrelated edit is saved.
 *
 *  A number does NOT get `type="number"`, deliberately. That input reports an
 *  empty string for anything it cannot parse, so a mistyped "12..5" arrives
 *  here indistinguishable from a cleared box — and an emptied box sends
 *  `null`. A typo would erase a lab value. Text plus `inputMode` keeps the
 *  numeric keypad on a phone while leaving the parse, and the refusal, here
 *  where they can be seen. */
function inputType(control: EditControl): string {
  switch (control) {
    case "date":
      return "date";
    case "datetime":
      return "datetime-local";
    default:
      return "text";
  }
}

export function FieldEdit({
  field,
  label,
  entry,
  control,
  value,
  units,
  onSave,
  onOpenChange,
}: FieldEditProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | string[]>(() => draftFrom(value, control));
  const [error, setError] = useState<string | null>(null);
  const firstControl = useRef<HTMLInputElement | HTMLSelectElement | null>(null);
  const pencil = useRef<HTMLButtonElement | null>(null);
  // Where focus goes when the editor closes. Left alone it falls to <body>,
  // and a keyboard or screen-reader user loses their place in a table of
  // fifty rows every time they save or cancel.
  const returnFocus = useRef(false);

  // The editor opens on the value the page is showing. Reopening after the
  // record changed underneath — a save elsewhere, a refetch — must not bring
  // back the value from last time.
  useEffect(() => {
    if (!open) setDraft(draftFrom(value, control));
  }, [open, value, control]);

  useEffect(() => {
    if (open) {
      firstControl.current?.focus();
      return;
    }
    if (returnFocus.current) {
      returnFocus.current = false;
      pencil.current?.focus();
    }
  }, [open]);

  if (!open) {
    return (
      <button
        type="button"
        ref={pencil}
        className="exact-elig__edit"
        aria-label={`Edit ${label}`}
        onClick={() => {
          setError(null);
          setOpen(true);
          onOpenChange?.(true);
        }}
      >
        <PencilIcon />
      </button>
    );
  }

  const options = optionsOf(entry);
  // Values the option list does not carry — a legacy spelling, a code retired
  // from the vocabulary — otherwise select nothing. In a single select the box
  // then reads "—" while an untouched Save sends the old value back: the
  // screen says cleared and the wire says unchanged. In a multiselect it is
  // worse, because the value is invisible AND the next change drops it: the
  // browser reports only what is selected, so a reader adding one marker
  // would silently delete another they cannot see.
  //
  // Not hypothetical for this field. PROMOP's own validator keeps values that
  // have no approved concept mapping precisely because imported text turns up
  // in it, so the list of allowed values is deliberately wider than the
  // curated options.
  const held = Array.isArray(draft) ? draft : draft === "" ? [] : [draft];
  const known = new Set(options.map((o) => o.value));
  const withCurrent = [
    ...options,
    ...held
      .filter((v) => v !== "" && !known.has(v))
      .map((v) => ({ value: v, label: `${v} (not in the list)` })),
  ];
  const unit = units ?? entry.unit;

  const commit = () => {
    const payload = payloadFrom(draft, control);
    if (typeof payload === "number" && Number.isNaN(payload)) {
      setError("Enter a number.");
      return;
    }
    setError(null);
    onSave(payload);
    returnFocus.current = true;
    setOpen(false);
    onOpenChange?.(false);
  };

  const cancel = () => {
    setError(null);
    returnFocus.current = true;
    setOpen(false);
    onOpenChange?.(false);
  };

  return (
    <div
      className="exact-elig__editor"
      data-field={field}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          cancel();
          return;
        }
        // Not in a multiselect: there Enter is how a keyboard user toggles
        // the option under the cursor, and committing instead would save a
        // selection they were still building.
        if (e.key === "Enter" && control !== "multiselect") {
          e.preventDefault();
          commit();
        }
      }}
    >
      {control === "select" || control === "boolean" ? (
        <select
          aria-label={label}
          ref={(el) => {
            firstControl.current = el;
          }}
          className="exact-elig__input"
          value={typeof draft === "string" ? draft : ""}
            onChange={(e) => setDraft(e.target.value)}
        >
          <option value="">—</option>
          {control === "boolean"
            ? [
                <option key="yes" value="true">Yes</option>,
                <option key="no" value="false">No</option>,
              ]
            : withCurrent.map((o) => (
                <option key={`opt:${o.value}`} value={o.value}>
                  {o.label}
                </option>
              ))}
        </select>
      ) : control === "multiselect" ? (
        <select
          aria-label={label}
          ref={(el) => {
            firstControl.current = el;
          }}
          multiple
          className="exact-elig__input exact-elig__input--multi"
          value={Array.isArray(draft) ? draft : []}
            onChange={(e) =>
            setDraft(Array.from(e.target.selectedOptions).map((o) => o.value))
          }
        >
          {withCurrent.map((o) => (
            <option key={`opt:${o.value}`} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          aria-label={label}
          ref={(el) => {
            firstControl.current = el;
          }}
          className="exact-elig__input"
          type={inputType(control)}
          inputMode={control === "number" ? "decimal" : undefined}
          value={typeof draft === "string" ? draft : ""}
            onChange={(e) => setDraft(e.target.value)}
        />
      )}
      {unit ? <span className="exact-elig__units">{unit}</span> : null}
      <button
        type="button"
        className="exact-elig__save"
        onClick={commit}
      >
        Save
      </button>
      <button type="button" className="exact-elig__cancel" onClick={cancel}>
        Cancel
      </button>
      {error ? (
        <span className="exact-elig__error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}
