// The age row's edit surface: the birth date AND its precision, in one save.
//
// Saving them as two independent subform entries is a trap the review found:
// on a year-only record, saving a full date first truncates it to January 1st
// against the old precision, and saving "day" afterwards promotes that
// placeholder to a supposedly complete date. The two values are one answer, so
// they leave together and the queue merges them into a single PATCH.

import { useState } from "react";

import { Dialog } from "./Dialog";
import type { RowEditing } from "./TrialDetailPage";
import type { SubformEntry } from "./types";

const PRECISIONS = [
  { value: "year", label: "Year only" },
  { value: "month", label: "Month and year" },
  { value: "day", label: "Full date" },
];

export interface BirthDateDialogProps {
  label: string;
  entries: SubformEntry[];
  editing: RowEditing;
  onClose: () => void;
}

/** An ISO date for `<input type="date">`, or "" for nothing.
 *
 *  The subform entry carries a date object through the API and a string in
 *  tests; both are normalised here, and anything with a time or an offset is
 *  cut to the date the box can actually show.
 */
function dateDraft(value: unknown): string {
  if (value == null || value === "") return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  return match ? `${match[1]}-${match[2]}-${match[3]}` : "";
}

export function BirthDateDialog({ label, entries, editing, onClose }: BirthDateDialogProps) {
  const date = entries.find((entry) => entry.upatientField === "date_of_birth");
  const precision = entries.find(
    (entry) => entry.upatientField === "date_of_birth_precision",
  );
  const [draft, setDraft] = useState(() => dateDraft(date?.value));
  const [precisionValue, setPrecisionValue] = useState(
    () => (typeof precision?.value === "string" && precision.value) || "day",
  );
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    if (!draft) {
      setError("Enter the date of birth.");
      return;
    }
    // Two calls in the same tick: the field queue waits a beat before sending
    // and merges them into one request, so PROMOP sees the date and the
    // precision together and cannot truncate against the old precision.
    editing.save("date_of_birth", draft);
    editing.save("date_of_birth_precision", precisionValue);
    onClose();
  };

  return (
    <Dialog label={label} title={label} onClose={onClose}>
      <p className="exact-subform__note">
        The age is calculated from this date. Choose how much of the date is
        known — “Year only” keeps the month and day unknown rather than
        guessing them.
      </p>

      <label className="exact-answer__field">
        <span>Date of birth</span>
        <input
          type="date"
          value={draft}
          aria-label="Date of birth"
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>

      <label className="exact-answer__field">
        <span>How precise is the date?</span>
        <select
          value={precisionValue}
          aria-label="Date of birth precision"
          onChange={(event) => setPrecisionValue(event.target.value)}
        >
          {PRECISIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      {error ? (
        <p className="exact-elig__error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="exact-answer__actions">
        <button type="button" onClick={save}>
          Save
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Dialog>
  );
}
