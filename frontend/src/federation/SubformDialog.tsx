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
//
// Which inputs it can actually offer is a narrower question than "does the
// row have any" — see `entryIsOffered`.

import { useState } from "react";

import { Dialog } from "./Dialog";
import { FieldEdit } from "./FieldEdit";
import { editabilityOf } from "./writable";
import type { Editability } from "./writable";
import { formatValue } from "./TrialDetailPage";
import type { RowEditing } from "./TrialDetailPage";
import type { SubformEntry, TrialDetailField } from "./types";

export interface SubformDialogProps {
  field: TrialDetailField;
  entries: SubformEntry[];
  editing: RowEditing;
  onClose: () => void;
}

/** Whether an entry is one this dialog can actually offer.
 *
 *  Two refusals. EXACT recomputing it is the first: the write would be undone.
 *  PROMOP refusing it is the second — most of the ULN groups' own labs are
 *  `alias` entries there, mirrors of a canonical field, so the lab a
 *  threshold is about is not writable at all.
 *
 *  Demographics used to be a third. `gender`, `ethnicity` and `date_of_birth`
 *  sit in the ULN groups because the limit is calculated against them, and
 *  they project onto the OMOP PERSON, not onto a measurement. An earlier
 *  version refused them as "a door into the wrong room"; the reader who is
 *  looking at a wrong ratio, however, needs exactly those fields — refusing
 *  them leaves the row unanswerable. They are offered now, and the dialog
 *  says out loud that they apply to the whole patient record, which is the
 *  part that was actually missing.
 */
function verdictFor(entry: SubformEntry, editing: RowEditing): Editability {
  // Recomputed here means EXACT derives it, so there is nothing to say and
  // nobody to say it to: the row upstairs already carries that story.
  if (entry.upatientRecomputed) return { can: "unknown" };
  // The dialog passes the row's vocabulary for the same reason the detail
  // page does, but the ROW branch can only ever produce a single select here:
  // the server overwrites a subform entry's type with `select` whenever it
  // attaches options at all (`trial_attributes.py:882-884`). A descriptor
  // that carries its own options and `multiple` still yields a multiselect,
  // as it does anywhere else. Passed rather than hard-coded, so the day the
  // server stops overwriting this does not have to be found again.
  return editabilityOf(entry.upatientField, editing.fields, {
    type: entry.type,
    options: entry.options,
  });
}

function entryIsOffered(entry: SubformEntry, editing: RowEditing): boolean {
  return verdictFor(entry, editing).can === "edit";
}

/** Whether a subform is worth opening at all.
 *
 *  Not "does the row have entries": a dialog with nothing editable in it is a
 *  door onto a wall, and one whose only editable entries are demographics is
 *  worse.
 */
export function subformCanBeEdited(
  entries: SubformEntry[] | null | undefined,
  editing: RowEditing | undefined,
): boolean {
  if (!entries?.length || !editing) return false;
  return entries.some((entry) => entryIsOffered(entry, editing));
}

const PRECISIONS = [
  { value: "year", label: "Year only" },
  { value: "month", label: "Month and year" },
  { value: "day", label: "Full date" },
];

function dateDraft(value: unknown): string {
  if (value == null || value === "") return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  return match ? `${match[1]}-${match[2]}-${match[3]}` : "";
}

/** The birth date and its precision as ONE row.
 *
 *  Every ULN group that reads an age now carries both entries. Saving them
 *  through the generic per-entry controls would truncate a full date against
 *  a year-only precision and then promote the placeholder — the trap
 *  `BirthDateDialog` was built for. Two calls in the same tick are merged by
 *  the field queue into one PATCH, so the date and precision land together.
 */
function BirthDateRow({
  entries,
  editing,
}: {
  entries: SubformEntry[];
  editing: RowEditing;
}) {
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
    setError(null);
    editing.save("date_of_birth", draft);
    editing.save("date_of_birth_precision", precisionValue);
  };

  return (
    <li className="exact-subform__row">
      <span className="exact-subform__label">{date?.label ?? "Date Of Birth"}</span>
      <input
        type="date"
        value={draft}
        aria-label={date?.label ?? "Date Of Birth"}
        onChange={(event) => setDraft(event.target.value)}
      />
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
      <button type="button" onClick={save}>
        Save date
      </button>
      {error ? (
        <span className="exact-elig__error" role="alert">
          {error}
        </span>
      ) : null}
    </li>
  );
}

export function SubformDialog({ field, entries, editing, onClose }: SubformDialogProps) {
  const hasBirthPair = entries.some(
    (entry) => entry.upatientField === "date_of_birth",
  ) && entries.some(
    (entry) => entry.upatientField === "date_of_birth_precision",
  );
  // These entries change the PERSON, not the measurement the row is about.
  // That is legitimate here — they are the numerator/denominator — but the
  // reader has to know the change reaches beyond this row.
  const patientWide = entries.some((entry) => {
    const projection = entry.upatientField
      ? editing.fields?.[entry.upatientField]?.projection_target
      : undefined;
    return projection === "person" || projection === "location";
  });

  return (
    <Dialog
      label={`${field.label}: the values it is computed from`}
      title={field.label}
      onClose={onClose}
    >
        <p className="exact-subform__note">
          This value is worked out from the ones below. Change those and it
          follows.
        </p>
        {patientWide ? (
          <p className="exact-subform__note">
            Gender, ethnicity and date of birth apply to the whole patient
            record, not only to this row.
          </p>
        ) : null}

        <ul className="exact-subform__list">
          {entries.map((entry) => {
            if (hasBirthPair && entry.upatientField === "date_of_birth_precision") {
              return null;
            }
            if (hasBirthPair && entry.upatientField === "date_of_birth") {
              return <BirthDateRow key={entry.name} entries={entries} editing={editing} />;
            }
            const editable = verdictFor(entry, editing);
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
                {/* The row's own formatter, not `String`: these entries carry
                    codes where the row shows labels (`er_minus` → "ER-"),
                    booleans where it shows Yes/No, and for the JSON ones a
                    nested control structure that stringifies to
                    "[object Object]". */}
                <span className="exact-subform__value">
                  {formatValue(shown, entry.options)}
                </span>
                {entry.uunits ?? entry.units ? (
                  <span className="exact-elig__units">
                    {entry.uunits ?? entry.units}
                  </span>
                ) : null}
                {outstanding ? (
                  <span className="exact-elig__saving">Saving…</span>
                ) : null}
                {failed ? (
                  <span className="exact-elig__error" role="alert">
                    Couldn't save that.
                  </span>
                ) : null}
                {/* The same rule as the detail page: a refusal that tells
                    the reader where the edit belongs is worth a sentence, and
                    one that only says the value is derived is not. Until now
                    this dialog printed neither, so `genetic_mutations` —
                    reachable here through the mutation subforms — read as a
                    value the record simply cannot hold. */}
                {editable.can === "no" && editable.announce ? (
                  <span className="exact-elig__note">{editable.why}</span>
                ) : null}
                {editable.can === "edit" ? (
                  <FieldEdit
                    field={editable.field}
                    label={entry.label}
                    entry={editable.entry}
                    control={editable.control}
                    options={editable.options}
                    joined={editable.joined}
                    units={entry.uunits ?? entry.units}
                    value={
                      failed && attribute
                        ? editing.failed[attribute]
                        : outstanding
                          ? pending
                          : entry.value
                    }
                    // A subform entry IS the patient attribute, so its
                    // `value` is the record's — with four exceptions the
                    // server rewrites for display (`get_value` in
                    // `trial_attributes.py:235-248`: `tumorGrade*` to a
                    // label, `supportiveTherapies`, `laterTherapies` and
                    // `geneticMutations` to derived structures). None can
                    // reach the comparison this feeds, which is read only
                    // for a multiselect. Not because the server forces a
                    // subform entry with options to `select` — that covers
                    // only `controlFor`'s row branch, and two of the four
                    // (`supportiveTherapies`, `laterTherapies`) really are
                    // subform entries. It is because the descriptor branch
                    // needs `multiple`, which PROMOP declares on two
                    // attributes and neither is these. If PROMOP declares it
                    // on one of them, this line has to change with it.
                    recordValue={entry.value}
                    onSave={(value) => editing.save(editable.field, value)}
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
    </Dialog>
  );
}
