// The dedicated editor for an INVERTED trial row (#5667).
//
// The row asks the negated question ("No HIV required") and the record stores
// the positive status. `1ceb009` crossed the polarity through the generic
// editor; that works but shows "Yes"/"No" over a clinical pair whose answers
// are "HIV Positive" / "No HIV", and it leaves the reader guessing which way
// the box runs.
//
// So this dialog asks the canonical question in the canonical words. It reads
// the trial's requirement from the row, the two answer labels from the
// descriptor's `answer_labels` on the positive field, and the starting value
// from the provenance the server captured at the raw boundary — never from
// `uvalue`, which cannot tell "answered unknown" from "never in the context".
//
// There is no inversion inside this component. The option value IS the value
// written: `positive` -> true, `negative` -> false, `unanswered` -> null. The
// row does the inversion on screen, which is the only place it belongs.

import { useState } from "react";

import { Dialog } from "./Dialog";
import type { RowEditing } from "./TrialDetailPage";
import type { NegativeSource, TrialDetailField } from "./types";

export interface NegatedBooleanDialogProps {
  field: TrialDetailField;
  /** The positive field a write lands in. */
  canonical: string;
  /** `answer_labels.true` from the canonical descriptor entry. */
  positive: string;
  /** `answer_labels.false` from the canonical descriptor entry. */
  negative: string;
  /** `available: true`, with the raw value the record held. */
  source: NegativeSource;
  editing: RowEditing;
  onClose: () => void;
}

type Answer = "positive" | "negative" | "unanswered";

/** The trial's own requirement, said out loud.
 *
 *  Without it a `true`, `false` and `null` requirement all read as the same
 *  heading, and the row's label alone does not say which one this trial is.
 */
function requirementLine(field: TrialDetailField): string {
  if (field.value === true) return `This trial requires “${field.label}”.`;
  if (field.value === false) {
    return `This trial explicitly does not require “${field.label}”.`;
  }
  return `This trial states no requirement on “${field.label}”.`;
}

function answerOf(value: unknown): Answer {
  return value === true ? "positive" : value === false ? "negative" : "unanswered";
}

export function NegatedBooleanDialog({
  field,
  canonical,
  positive,
  negative,
  source,
  editing,
  onClose,
}: NegatedBooleanDialogProps) {
  // Draft priority, in the order the reader's own actions happened: a refused
  // value, then one still on the wire, then the record. Membership is tested
  // by presence (`in`), never by truthiness: `false` is an answer, and so is
  // a cleared `null`.
  const failed = canonical in editing.failed;
  const outstanding = canonical in editing.outstanding;
  const seed = failed
    ? editing.failed[canonical]
    : outstanding
      ? editing.outstanding[canonical]
      : source.value;
  const [draft, setDraft] = useState<Answer>(answerOf(seed));

  const commit = () => {
    // One write, to the canonical field, with no crossing here. The queue
    // keys it the way the row looks it up, so `Saving…` and a refusal land on
    // this row rather than disappearing.
    editing.save(
      canonical,
      draft === "positive" ? true : draft === "negative" ? false : null,
    );
    onClose();
  };

  return (
    <Dialog
      label={`${field.label}: your answer`}
      title={field.label}
      onClose={onClose}
    >
      <p className="exact-subform__note">{requirementLine(field)}</p>
      <p className="exact-subform__note">
        The record stores the positive answer; this row shows the opposite.
      </p>
      <fieldset className="exact-subform__list">
        <legend className="exact-elig__colhdr">{positive}?</legend>
        {(
          [
            ["positive", positive],
            ["negative", negative],
            ["unanswered", "Not answered"],
          ] as const
        ).map(([value, label]) => (
          <label key={value} className="exact-subform__row">
            <input
              type="radio"
              name={`negated-${canonical}`}
              value={value}
              checked={draft === value}
              onChange={() => setDraft(value)}
            />
            <span className="exact-subform__label">{label}</span>
          </label>
        ))}
      </fieldset>
      <div>
        <button type="button" className="exact-elig__save" onClick={commit}>
          Save
        </button>
        <button type="button" className="exact-elig__cancel" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Dialog>
  );
}
