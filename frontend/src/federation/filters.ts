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

/** The panel fields the reader has actually changed, as a partial filter set.
 *
 *  What gets SAVED, as opposed to what gets sent to the search. The baseline
 *  carries the host's mount-time scope — the seeded country, an
 *  `initialFilters.register` or `recruitmentStatus` the host chose for this
 *  mount — and persisting those would turn one mount's scope into the reader's
 *  standing preference: a later mount with a different scope would find the
 *  stale one saved and the overlay would win.
 *
 *  A field the reader cleared back to nothing is present with `undefined`, not
 *  absent. The transport carries what the server already holds and writes the
 *  reader's set over it, so a key that is merely missing keeps its stored
 *  value; a key that is present-and-undefined is the one that goes.
 *
 *  `owned` names fields already known to be the reader's — what a previous
 *  mount loaded from storage, plus everything persisted since. Ownership is
 *  sticky because equality to the current baseline is not evidence of its
 *  absence: a reader who saved a 50-mile radius against a 50-KM baseline owns
 *  a field whose value matches, and deriving ownership from the diff alone
 *  would drop it on the next unrelated edit and take the units with it.
 */
export function userOwnedFilters(
  filters: FilterState,
  baseline: FilterState,
  owned: ReadonlySet<string> = new Set(),
): FilterState {
  const out: FilterState = {};
  for (const field of PANEL_FIELDS) {
    const value = filters[field];
    const base = baseline[field];
    if (isInactive(field, value) && isInactive(field, base)) {
      // An OWNED field that is now empty is emitted as `undefined` — a
      // tombstone, not an omission. Both transports treat a key that is
      // simply absent as "no opinion, keep what you have", so a cleared
      // filter would survive storage and be applied again on the next
      // mount.
      if (!owned.has(field)) continue;
      (out as Record<string, unknown>)[field] = undefined;
      continue;
    }
    if (value === base && !owned.has(field)) continue;
    (out as Record<string, unknown>)[field] = value;
  }
  // `distanceUnits` is not a PANEL_FIELD — it qualifies `distance` rather than
  // standing on its own, which is why it does not count toward the badge. It
  // still has to be SAVED whenever a distance is active, or a radius chosen in
  // miles comes back as the same number of kilometres.
  //
  // Not keyed on `distance` reaching `out`: the units control is enabled for a
  // host-seeded distance too, so a reader can switch 50 km to 50 miles without
  // changing the number, which leaves `distance` equal to the baseline and
  // absent from `out`. Keyed on the units having actually been chosen, though
  // — saving a unit the HOST seeded would make one mount's scope the reader's
  // standing preference, and a later mount's 50-mile host radius would come
  // back as 50 km.
  if ("distance" in out && out.distance === undefined) {
    // The units leave with the distance they qualified — the panel treats them
    // that way, and the unit would otherwise be kept in storage and applied
    // to whatever radius comes next.
    out.distanceUnits = undefined;
  } else if (
    isActiveDistance(filters.distance) &&
    filters.distanceUnits !== undefined &&
    ("distance" in out || filters.distanceUnits !== baseline.distanceUnits)
  ) {
    out.distanceUnits = filters.distanceUnits;
  }
  return out;
}

/** Whether the panel is showing anything other than the baseline — drives
 *  whether Reset is worth offering. */
export function hasActiveFilters(
  filters: FilterState,
  baseline: FilterState,
): boolean {
  return countActiveFilters(filters, baseline) > 0;
}
