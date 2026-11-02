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

import { Dialog } from "./Dialog";
import { FieldEdit } from "./FieldEdit";
import { editabilityOf } from "./writable";
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
 *  Three refusals, and the third is the one that is easy to miss.
 *
 *  EXACT recomputing it is the first: the write would be undone.
 *
 *  PROMOP refusing it is the second — most of the ULN groups' own labs are
 *  `alias` entries there, mirrors of a canonical field, so the lab a
 *  threshold is about is not writable at all.
 *
 *  And the demographics are the third. `gender` and `ethnicity` sit in six
 *  subform groups, because a creatinine or bilirubin limit is calculated
 *  against them — but they project onto the OMOP PERSON, not onto any
 *  measurement. They are writable, so counting them makes a dialog captioned
 *  "the values it is computed from" whose only two controls change the
 *  patient's demographics, record-wide, from a threshold row. That is not a
 *  door onto a wall; it is a door into the wrong room.
 */
function entryIsOffered(entry: SubformEntry, editing: RowEditing): boolean {
  if (entry.upatientRecomputed) return false;
  const verdict = editabilityOf(entry.upatientField, editing.fields);
  if (verdict.can !== "edit") return false;
  const projection = verdict.entry.projection_target;
  return projection !== "person" && projection !== "location";
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

export function SubformDialog({ field, entries, editing, onClose }: SubformDialogProps) {
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

        <ul className="exact-subform__list">
          {entries.map((entry) => {
            const editable = entryIsOffered(entry, editing)
              ? editabilityOf(entry.upatientField, editing.fields)
              : ({ can: "unknown" } as const);
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
                {editable.can === "edit" ? (
                  <FieldEdit
                    field={editable.field}
                    label={entry.label}
                    entry={editable.entry}
                    control={editable.control}
                    units={entry.uunits ?? entry.units}
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
    </Dialog>
  );
}
