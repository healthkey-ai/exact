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

/** Filter fields that hold several values. Listed rather than inferred: the
 *  coercion below has to know what SHOULD be an array, not what happens to be
 *  one in the value it was handed. */
const MULTI_FIELDS = ["trialPurpose"] as const;

/** Coerce a FilterState that came from outside this build.
 *
 *  `trialPurpose` was a single string until CB #4663 (#428). Two sources still
 *  hold the old shape and neither is under our control:
 *
 *  * saved filters — `userOwnedFilters` persisted `trialPurpose: "treatment"`
 *    to localStorage or, through the state adapter, to PROMOP. The PROMOP copy
 *    is per person and survives clearing the browser.
 *  * a host's `initialFilters` — `FilterState` is a published type, and a host
 *    compiles separately, so `tsc` here never sees its value.
 *
 *  Read back unchanged, a string reaches `filterStateToParams` with a truthy
 *  `.length` and throws on `.join` — the trial list then errors on every
 *  mount, and re-saves the string, so it does not heal itself. In the panel it
 *  is worse than a crash: `"treatment".includes("treatment")` is true, so the
 *  box renders ticked, and one click spreads the string into its own letters.
 *
 *  Split on commas because that is the wire form this build emits, so a value
 *  round-tripped through a URL comes back as the list it went out as. */
export function normalizeFilterState(filters?: FilterState): FilterState {
  const out: FilterState = { ...filters };
  for (const field of MULTI_FIELDS) {
    const value = out[field] as unknown;
    if (value === undefined || value === null) continue;
    const codes = (Array.isArray(value) ? value : String(value).split(","))
      .map((code) => String(code).trim())
      .filter(Boolean);
    (out as Record<string, unknown>)[field] = codes.length ? codes : undefined;
  }
  return out;
}

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
  const base: FilterState = normalizeFilterState(initialFilters);
  const country = countryFor(patientCountry, initialFilters);
  if (country) base.country = country;
  return base;
}

function isEmpty(value: unknown): boolean {
  // An empty array is a filter nobody set. Without this line the multiselect
  // `trialPurpose` (#428) would read as active from the moment the control
  // initialised it to `[]`, so the Filters badge would open at 1 and Reset
  // would offer to clear a filter that was never applied.
  if (Array.isArray(value)) return value.length === 0;
  return value === undefined || value === null || value === "" || value === false;
}

/** Whether two filter values mean the same search.
 *
 *  `===` on a multi-value field compares array IDENTITY, so two equal
 *  selections read as different and every render would count the field as
 *  changed. Order-insensitive because the server answers with the union —
 *  `by_trial_purpose` ORs the codes — so a reordering is not a different
 *  search and must not light up the badge. Case-insensitive for the same
 *  reason: the codes are matched `iexact`. */
function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    const left = Array.isArray(a) ? a : [];
    const right = Array.isArray(b) ? b : [];
    if (left.length !== right.length) return false;
    const norm = (list: unknown[]) =>
      list.map((v) => String(v).toLowerCase()).sort();
    const [l, r] = [norm(left), norm(right)];
    return l.every((value, index) => value === r[index]);
  }
  return a === b;
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
    return sameValue(value, base) ? count : count + 1;
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
 *  absent, so the transport can clear it on the server rather than leaving the
 *  old value behind a merge.
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
      // tombstone, not an omission. The merge transports both treat a key
      // that is simply absent as "no opinion, keep what you have", so a
      // cleared filter would survive on disk and be applied again on the
      // next mount.
      if (!owned.has(field)) continue;
      (out as Record<string, unknown>)[field] = undefined;
      continue;
    }
    if (sameValue(value, base) && !owned.has(field)) continue;
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
    // that way, and a merge transport would otherwise keep the old unit on
    // disk, to be applied to whatever radius comes next.
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
