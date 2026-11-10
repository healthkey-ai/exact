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
import type { Editability } from "./writable";
import { resolveEditor, toEditability } from "./editorResolver";
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
 *  Two refusals. EXACT recomputing it is the first: the write would be
 *  undone. PROMOP refusing it is the second — most of the ULN groups' own
 *  labs are `alias` entries there, mirrors of a canonical field, so the lab a
 *  threshold is about is not writable at all.
 *
 *  Person-scoped demographics ARE offered: `gender` and `race` sit in six
 *  subform groups because a creatinine or bilirubin limit is calculated
 *  against them. That is exactly why the dialog, captioned "the values it is
 *  computed from", is where they belong — and it is what native CB does.
 */
function verdictFor(
  entry: SubformEntry,
  editing: RowEditing,
  parentField?: string | null,
): Editability {
  if (!entry.upatientField) return { can: "unknown" };
  // The same resolver as the row path, told that this is a subform entry:
  // recompute, person-scoped demographics, aliases and the unmapped-vocabulary
  // guard all live in one place now. `toEditability` adapts the decision to
  // the editor this dialog already draws.
  return toEditability(
    resolveEditor(entry.upatientField, editing.fields, {
      surface: "subform",
      entry,
      parentField,
    }),
    editing.fields,
  );
}

function entryIsOffered(
  entry: SubformEntry,
  editing: RowEditing,
  parentField?: string | null,
): boolean {
  return verdictFor(entry, editing, parentField).can === "edit";
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
  parentField?: string | null,
): boolean {
  if (!entries?.length || !editing) return false;
  return entries.some((entry) => entryIsOffered(entry, editing, parentField));
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
            const editable = verdictFor(entry, editing, field.upatientField);
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
