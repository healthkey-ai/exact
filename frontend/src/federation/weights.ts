/** The Suitability Score's four weights — CB's `WeightSettingsForm`.
 *
 *  They are not filters. They change the ORDER of the list and the percentage
 *  on each card, never which trials are in it, which is why they live outside
 *  `PANEL_FIELDS`: the Filters badge does not count them and Reset does not
 *  clear them. They ride in `FilterState` all the same, so the query key, the
 *  saved-settings row and the export all carry them without a second seam.
 */
import type { FilterState } from "./types";

/** What the server uses for a weight it was not sent. */
export const DEFAULT_WEIGHT = 25;

/** The top of the range the form offers, and the same one storage is read
 *  through. It is a UI convention rather than a matcher constraint — the
 *  score divides the four by their sum, so only their ratio is ever read —
 *  but it is the range the reader is told about, and a number the form would
 *  refuse should not be reachable through another door (#538). */
export const MAX_WEIGHT = 100;

export interface WeightField {
  key: WeightKey;
  /** CB's label, verbatim. */
  label: string;
  /** What this term actually measures, for the reader who has to choose. */
  hint: string;
}

export type WeightKey =
  | "benefitWeight"
  | "patientBurdenWeight"
  | "riskWeight"
  | "distancePenaltyWeight";

/** CB's order, which is alphabetical by key rather than by meaning. Kept
 *  because a reader who knows one screen should recognise the other. */
export const WEIGHT_FIELDS: readonly WeightField[] = [
  {
    key: "benefitWeight",
    label: "Benefit Weight",
    hint: "How much the possible benefit of the treatment counts.",
  },
  {
    key: "distancePenaltyWeight",
    label: "Distance Penalty Weight",
    hint: "How much a trial counts against itself for being far from you.",
  },
  {
    key: "patientBurdenWeight",
    label: "Patient Burden Weight",
    hint: "How much the demands on you count — visits, procedures, time.",
  },
  {
    key: "riskWeight",
    label: "Risk Weight",
    hint: "How much the possible harms of the treatment count.",
  },
];

/** A weight this UI will use: a finite number inside the range it offers.
 *
 *  The server guards itself — negatives clamp to zero, non-finite values are
 *  dropped, an all-zero set falls back to 25s — but a value that cannot mean
 *  anything should not leave here in the first place, and a host's
 *  `initialFilters` reaches the wire without passing the form. The upper
 *  bound is the same one `sanitizeStoredFilters` reads storage through: the
 *  two used to disagree, so a host could put 500 on the wire and then watch
 *  the dialog refuse to save it, on a field the reader never touched. */
export function isUsableWeight(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= MAX_WEIGHT
  );
}

/** The weight to show for a field: what is set, or the server's default. */
export function weightValue(filters: FilterState, key: WeightKey): number {
  const value = filters[key];
  return isUsableWeight(value) ? value : DEFAULT_WEIGHT;
}

/** Whether these weights say anything the server would not have assumed. */
export function weightsAreCustom(filters: FilterState): boolean {
  return WEIGHT_FIELDS.some(({ key }) => {
    const value = filters[key];
    return isUsableWeight(value) && value !== DEFAULT_WEIGHT;
  });
}

/** The four as they should be stored: every field written, so that turning a
 *  weight back to 25 is saved as a decision rather than read as "no opinion"
 *  by a transport that merges. */
export function weightsToSave(weights: Record<WeightKey, number>): FilterState {
  const out: FilterState = {};
  for (const { key } of WEIGHT_FIELDS) out[key] = weights[key];
  return out;
}
