// Pure logic behind the filter panel: what counts as an active filter, and
// what "reset" means. Kept out of the component so the node-environment
// suite can test it (see vitest.config.ts, and #426 for the component-level
// gap).

import type { FilterState } from "./types";

export interface DistanceUnitOption {
  value: "km" | "miles";
  label: string;
}

/** The wire values `by_distance` understands. It compares against `miles`
 *  and treats everything else as kilometres, so CB's `kilometers` works but
 *  is echoed back verbatim into the response's `distanceUnits` and would be
 *  rendered as "743 kilometers". Send `km`. */
export const DISTANCE_UNITS: DistanceUnitOption[] = [
  { value: "miles", label: "miles" },
  { value: "km", label: "km" },
];

/** Filters the panel owns. `type` and `sort` are excluded on purpose: the
 *  tab bar and the sort control own those, and counting them would make the
 *  "Filters (N)" badge tick up when the user switches tab. */
const PANEL_FIELDS = [
  "searchTitle",
  "searchTreatment",
  "sponsor",
  "trialType",
  "trialPurpose",
  "recruitmentStatus",
  "phase",
  "register",
  "lastUpdate",
  "distance",
  // No control of their own — `country` is seeded from the patient, and
  // these three can only arrive through the host's `initialFilters`. They
  // are counted and cleared all the same, so the badge cannot read 0 while
  // a filter is running and Reset cannot leave one behind.
  "country",
  "region",
  "studyType",
  "validatedOnly",
] as const;

export type PanelField = (typeof PANEL_FIELDS)[number];

/** Which country the list should be scoped to.
 *
 *  The patient's own wins, because it is the more specific fact — but only
 *  when there is one; otherwise a host-supplied default stands. Exported
 *  because both the baseline and the component's seeding need the answer,
 *  and when the two were written separately they disagreed: the seed set
 *  `undefined` where the baseline kept the host's country, so the badge
 *  claimed a filter that was not on the wire and Reset *changed* the
 *  result set. */
export function countryFor(
  patientCountry: string | undefined,
  initialFilters?: FilterState,
): string | undefined {
  // `||`, not `??`: an empty string is not a country. With `??` this kept
  // `""` while the baseline's own `if (country)` dropped it — the two
  // answers diverging again, on the one helper whose point is that they
  // cannot. Unreachable from the component, which trims and normalizes
  // first, but this is exported.
  return patientCountry || initialFilters?.country;
}

/** The state the panel resets to.
 *
 *  Not simply `{}`: `country` is seeded from the patient's own profile, so
 *  clearing it would silently widen the search to every country rather than
 *  restoring the default, and a host's own initial filters are part of the
 *  default too. The baseline carries whatever the patient and the host imply.
 */
export function baselineFilters(
  patientCountry: string | undefined,
  initialFilters?: FilterState,
): FilterState {
  // The host's own initial filters are part of the baseline, not something
  // Reset throws away: a host that mounts the remote already scoped to a
  // register or a recruitment status means that scope to survive the button.
  const base: FilterState = { ...initialFilters };
  const country = countryFor(patientCountry, initialFilters);
  if (country) base.country = country;
  return base;
}

function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || value === "" || value === false;
}

/** Whether a distance is a radius the backend will actually honour.
 *
 *  Only a positive number is. Zero applies no limit — the backend gates on
 *  `if study_info.distance:` — and a negative one is worse: it passes that
 *  same truthiness check and becomes a negative geospatial radius, so the
 *  search comes back empty for a reason nothing on screen explains. The
 *  control cannot produce either, but the public `FilterState` permits both
 *  through a host's `initialFilters`, so every consumer asks here. */
export function isActiveDistance(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function isInactive(field: PanelField, value: unknown): boolean {
  if (field === "distance") return !isActiveDistance(value);
  return isEmpty(value);
}

/** How many filters the user has actually changed — the number CB shows on
 *  its Filters button (its own count comes from the server, which knows the
 *  stored defaults; here the baseline stands in for them).
 *
 *  Compared against the baseline rather than against emptiness so a country
 *  that merely matches the patient's own does not read as a filter the user
 *  applied. `distanceUnits` is not counted: it qualifies `distance` and
 *  cannot be set without it. */
export function countActiveFilters(
  filters: FilterState,
  baseline: FilterState,
): number {
  return PANEL_FIELDS.reduce((count, field) => {
    const value = filters[field];
    const base = baseline[field];
    if (isInactive(field, value) && isInactive(field, base)) return count;
    return value === base ? count : count + 1;
  }, 0);
}

/** Whether the panel is showing anything other than the baseline — drives
 *  whether Reset is worth offering. */
export function hasActiveFilters(
  filters: FilterState,
  baseline: FilterState,
): boolean {
  return countActiveFilters(filters, baseline) > 0;
}

/** The fields that are saved to the patient's stored preferences.
 *
 *  Exactly the controls the panel renders, and nothing else. Three kinds of
 *  field are deliberately absent:
 *
 *  - `type` and `sort` belong to the tab bar and the sort control.
 *  - `country` is derived from the patient's own profile and has no control
 *    (#430). Storing it would store a copy of a fact that can change.
 *  - `region`, `studyType` and `validatedOnly` can only arrive through a
 *    host's `initialFilters`. Saving them would pin the host's scope into
 *    the patient's own preferences, where it would outlive a host that had
 *    stopped sending it — a filter running with nothing on screen that can
 *    turn it off.
 *  - `trialType` is absent for a reason of its own: it is scoped to the
 *    disease. A type chosen for one has no matching option under another,
 *    and `by_trial_type` has no leniency for a value that is not there —
 *    an empty result set from a control rendering blank. The component
 *    already treats a type as belonging to the patient it was picked for
 *    (`trialTypeOwner`); a stored one would arrive unclaimed, so nothing
 *    would ever make it stale. Persisting it needs the panel to check it
 *    against the disease's own options first, which is #437.
 */
export const PERSISTED_FIELDS = [
  "searchTitle",
  "searchTreatment",
  "sponsor",
  "trialPurpose",
  "recruitmentStatus",
  "phase",
  "register",
  "lastUpdate",
  "distance",
  "distanceUnits",
] as const;

/** Longest stored string accepted. These travel on as query parameters, and
 *  nothing a person types into a search box is anywhere near this. */
const MAX_STORED_STRING = 200;

/** What to save: the persisted fields, with anything inactive left out so
 *  the stored object says only what the reader actually set.
 *
 *  A value the HOST set is left out too. The panel is fed the effective
 *  filters, so a host's `initialFilters` ride along in every field it
 *  touched, and a save would copy them into the patient's own preferences —
 *  where they outrank the host on the next mount and outlive a host that
 *  stopped sending them. The rule the field list already states for
 *  `region` and friends, applied to the fields that do have controls. */
export function filtersToStore(
  filters: FilterState,
  hostFilters: FilterState = {},
  stored: FilterState = {},
): FilterState {
  const out: FilterState = {};
  for (const field of PERSISTED_FIELDS) {
    const value = filters[field];
    // Identical to what the host asked for: not the reader's choice to
    // store, and storing it changes nothing except who owns it.
    //
    // Unless the patient had already stored it. The two can agree by
    // coincidence — a host scoping to PHASE3 for someone whose saved phase
    // is PHASE3 — and since a save replaces the stored set whole, dropping
    // the field on that coincidence deletes their preference. It surfaces
    // the day the host stops sending it, which is exactly the day it was
    // supposed to still be there.
    // Deliberate consequence: once the patient has a stored value for a
    // field, whatever they select there is theirs — including a value that
    // happens to be the host's. The alternative makes choosing the value
    // the host already uses mean "delete my preference", which is a
    // surprising thing for a dropdown to do, and it cannot be told apart on
    // screen from choosing it on purpose.
    if (value === hostFilters[field] && stored[field] === undefined) continue;
    if (field === "distance") {
      if (isActiveDistance(value)) out.distance = value as number;
      continue;
    }
    if (field === "distanceUnits") continue; // settled after the loop

    if (typeof value === "string" && value !== "") {
      out[field] = value.slice(0, MAX_STORED_STRING) as never;
    }
  }
  // The unit is settled last, and outside the host-equality rule above.
  //
  // Only alongside a distance: on its own it is a unit for nothing. But a
  // stored radius must always carry one — subtracted because it matched the
  // host's, a saved 50 MILES came back as 50 km the day the host stopped
  // sending the unit, which is a different set of trials and nothing on
  // screen to say so.
  if (out.distance != null) {
    const unit = filters.distanceUnits;
    if (unit === "km" || unit === "miles") out.distanceUnits = unit;
  }
  return out;
}

/** What to trust on the way back.
 *
 *  The stored payload is opaque JSON on the server — PROMOP checks that it
 *  is an object and nothing more — and this remote is not its only writer.
 *  So a stored value is treated as a claim, not as a `FilterState`: unknown
 *  keys are dropped, and a value of the wrong type is dropped rather than
 *  handed to a control that expects a string and would render `[object
 *  Object]`, or sent to the backend to be rejected.
 */
export function sanitizeStoredFilters(raw: unknown): FilterState {
  // No array check: a JSON array has none of these keys, so it falls out
  // of the loop as `{}` anyway, and a guard that cannot change an answer is
  // a guard no test can pin.
  if (raw == null || typeof raw !== "object") return {};
  const input = raw as Record<string, unknown>;
  const out: FilterState = {};
  for (const field of PERSISTED_FIELDS) {
    const value = input[field];
    if (field === "distance") {
      if (isActiveDistance(value)) out.distance = value as number;
      continue;
    }
    if (field === "distanceUnits") {
      if (value === "km" || value === "miles") out.distanceUnits = value;
      continue;
    }
    if (typeof value === "string" && value !== "" && value.length <= MAX_STORED_STRING) {
      out[field] = value as never;
    }
  }
  // A unit without a distance qualifies nothing; drop it rather than let it
  // ride along into the request.
  if (out.distanceUnits != null && out.distance == null) delete out.distanceUnits;
  return out;
}
