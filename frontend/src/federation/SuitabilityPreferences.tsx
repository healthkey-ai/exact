// Suitability Preferences — what the Suitability Score should weigh.
//
// CB's button of this name opens a chat with a bot that asks the reader to
// rank four factors and writes the weights for them. There is no bot here,
// and a remote is the wrong place to grow one: what the reader would have
// been asked for indirectly is asked for directly, with CB's own
// `WeightSettingsForm` — the same four fields, the same labels, the same
// 0-100 — moved from CB's profile page to the button that promises it.
//
// Two deliberate departures from CB, both because this build can afford what
// CB could not:
//
//   - **The list updates.** CB stores the weights on the user row and its
//     trials query never sends them, so saving leaves the ranked list as it
//     was until something else refetches it. EXACT takes them as query
//     parameters, so they live in `FilterState` and a save re-runs the
//     search like any other change.
//   - **Defaults can be restored.** CB's "Reset" drops local edits back to
//     what the server holds, and nothing anywhere offers the 25/25/25/25 the
//     score starts from. Here that is one button, because a reader who has
//     lost track of what they changed has no other way back.
import { useEffect, useId, useState } from "react";

import { ActionTooltip } from "./bits";
import { Dialog } from "./Dialog";
import { ACTION_TOOLTIPS } from "./tooltips";
import {
  DEFAULT_WEIGHT,
  MAX_WEIGHT,
  WEIGHT_FIELDS,
  type WeightKey,
  isUsableWeight,
  weightValue,
  weightsAreCustom,
} from "./weights";
import type { FilterState } from "./types";

/** CB's `SparkleIcon`, redrawn at the size the rest of this chrome uses. */
const SparkleIcon = ({ size = 18 }: { size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    aria-hidden="true"
  >
    <path d="M12 2.5l1.7 4.6 4.6 1.7-4.6 1.7-1.7 4.6-1.7-4.6L5.7 8.8l4.6-1.7L12 2.5z" />
    <path d="M18.5 14.5l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9.9-2.4z" />
    <path d="M5.5 13l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9L2.9 15.6l1.9-.7L5.5 13z" />
  </svg>
);

type Draft = Record<WeightKey, string>;

/** Shown as it is stored. CB rounds here, because CB's own row holds these as
 *  decimal strings and the box would otherwise read "25.00" — this row holds
 *  numbers, and rounding a stored 40.4 to 40 would mean a reader who opened
 *  the dialog and pressed Save changed their score without touching it. */
const draftFrom = (filters: FilterState): Draft =>
  Object.fromEntries(
    WEIGHT_FIELDS.map(({ key }) => [key, String(weightValue(filters, key))]),
  ) as Draft;

/** CB's rule, and CB's words for it. The server is looser — it clamps a
 *  negative to zero and reads an all-zero set as no opinion at all — but a
 *  number outside this range is not something the reader meant. */
const RANGE_ERROR = `Must be between 0 and ${MAX_WEIGHT}`;
/** `<input type="number">` hands back "" for anything it cannot parse, so an
 *  empty box and "abc" arrive here identically — and neither is out of range,
 *  which is what CB's one message says. */
const MISSING_ERROR = `Enter a number between 0 and ${MAX_WEIGHT}`;

function readDraft(draft: Draft): {
  weights?: Record<WeightKey, number>;
  errors: Partial<Record<WeightKey, string>>;
} {
  const errors: Partial<Record<WeightKey, string>> = {};
  const weights = {} as Record<WeightKey, number>;
  for (const { key } of WEIGHT_FIELDS) {
    const raw = draft[key].trim();
    if (raw === "") {
      errors[key] = MISSING_ERROR;
      continue;
    }
    // The predicate, not a third copy of its comparison: the constant being
    // shared is only half the drift this closed.
    const value = Number(raw);
    if (!isUsableWeight(value)) {
      errors[key] = RANGE_ERROR;
      continue;
    }
    weights[key] = value;
  }
  return Object.keys(errors).length ? { errors } : { weights, errors };
}

export interface SuitabilityPreferencesProps {
  filters: FilterState;
  /** Who the weights would be saved for. The dialog closes when it changes:
   *  a host can switch patients with this open, and the draft in hand is the
   *  PREVIOUS reader's — saved then, it lands in the new patient's row. */
  patientKey: string;
  /** Apply and persist. The four always travel together — a partial set
   *  would be merged by the store into whatever it held before, which is
   *  how a reader ends up with a pair of weights they never chose. */
  onChange: (weights: Record<WeightKey, number>) => void;
}

export function SuitabilityPreferences({
  filters,
  patientKey,
  onChange,
}: SuitabilityPreferencesProps) {
  // Per instance, because two mounts on one page would otherwise emit the
  // same four input ids: a label in the second would focus a field in the
  // first, and `aria-describedby` would read out the first one's hint.
  const uid = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(filters));
  const [errors, setErrors] = useState<Partial<Record<WeightKey, string>>>({});

  // Not "re-seed the draft" — the reader was editing someone else's score,
  // and there is no answer to "which of these numbers did you mean for whom".
  useEffect(() => {
    setOpen(false);
  }, [patientKey]);

  const openDialog = () => {
    // Seeded on open, not held in step with `filters`: the dialog is the only
    // thing that writes them, and re-seeding while it is open would take the
    // reader's half-typed number away when the save lands.
    setDraft(draftFrom(filters));
    setErrors({});
    setOpen(true);
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const { weights, errors: found } = readDraft(draft);
    setErrors(found);
    if (!weights) return;
    onChange(weights);
    setOpen(false);
  };

  const custom = weightsAreCustom(filters);

  return (
    <>
      <ActionTooltip text={ACTION_TOOLTIPS.suitabilityPreferences} align="end">
        {(tipId) => (
          <button
            type="button"
            className="exact-prefs__trigger"
            aria-describedby={tipId}
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={openDialog}
          >
            <SparkleIcon />
            <span>Suitability Preferences</span>
            {/* Said in words, not by a dot: "changed" is the whole reason a
                reader opens this, and a coloured dot would need its own
                legend. The space is load-bearing — without it the accessible
                name runs the two together as "Preferenceschanged". */}
            {custom ? <> <span className="exact-prefs__badge">changed</span></> : null}
          </button>
        )}
      </ActionTooltip>

      {open ? (
        <Dialog
          label="Suitability Preferences"
          title="Suitability Preferences"
          onClose={() => setOpen(false)}
        >
          <p className="exact-subform__note">
            What the Suitability Score weighs. Only the sizes relative to each
            other matter, so 50/50/50/50 says the same thing as 25/25/25/25.
          </p>

          <form className="exact-prefs__form" onSubmit={submit} noValidate>
            {WEIGHT_FIELDS.map(({ key, label, hint }) => {
              const error = errors[key];
              return (
                <div className="exact-prefs__row" key={key}>
                  <label className="exact-prefs__label" htmlFor={`${uid}-${key}`}>
                    {label}
                  </label>
                  <span className="exact-prefs__hint" id={`${uid}-${key}-hint`}>
                    {hint}
                  </span>
                  <input
                    id={`${uid}-${key}`}
                    className={`exact-prefs__input${error ? " is-invalid" : ""}`}
                    type="number"
                    min="0"
                    max={MAX_WEIGHT}
                    // `any`, not `1`: the arrows still walk in whole numbers,
                    // and a fraction already stored does not read as invalid.
                    step="any"
                    inputMode="numeric"
                    value={draft[key]}
                    // The message too, while there is one: an alert is heard once,
                    // and a reader coming back to the field would otherwise be
                    // told it is invalid with no way to hear why.
                    aria-describedby={`${uid}-${key}-hint${error ? ` ${uid}-${key}-error` : ""}`}
                    aria-invalid={error ? true : undefined}
                    onChange={(event) => {
                      const next = event.target.value;
                      setDraft((current) => ({ ...current, [key]: next }));
                      // The message goes the moment the field it belongs to
                      // is touched; keeping it there while the reader fixes
                      // the number reads as a second, new complaint.
                      setErrors((current) =>
                        current[key] ? { ...current, [key]: undefined } : current,
                      );
                    }}
                  />
                  {error ? (
                    <span className="exact-prefs__error" id={`${uid}-${key}-error`} role="alert">
                      {error}
                    </span>
                  ) : null}
                </div>
              );
            })}

            <div className="exact-prefs__actions">
              <button
                type="button"
                className="exact-filters__trigger"
                onClick={() => {
                  setDraft(
                    Object.fromEntries(
                      WEIGHT_FIELDS.map(({ key }) => [key, String(DEFAULT_WEIGHT)]),
                    ) as Draft,
                  );
                  setErrors({});
                }}
              >
                Restore defaults
              </button>
              <button type="submit" className="exact-prefs__save">
                Save
              </button>
            </div>
          </form>
        </Dialog>
      ) : null}
    </>
  );
}
