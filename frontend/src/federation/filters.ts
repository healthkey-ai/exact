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
    // that way, and a merge transport would otherwise keep the old unit on
    // disk, to be applied to whatever radius comes next.
    out.distanceUnits = undefined;
  } else if (
    isActiveDistance(filters.distance) &&
    filters.distanceUnits !== undefined &&
    // `owned` like every other field. Without it this was the one saved
    // value that could not be REVERTED: switching the host's km to miles
    // stored the miles, and switching back matched the baseline again, so
    // nothing was written and the stored miles stood. On a transport that
    // only merges — the localStorage one — the reader could not get back
    // to kilometres except through Reset.
    ("distance" in out ||
      owned.has("distanceUnits") ||
      filters.distanceUnits !== baseline.distanceUnits)
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

/** The "Updated within" choices, and the only stored field whose options
 *  are decidable here rather than arriving per disease from
 *  `/form-settings/`.
 *
 *  Exported so `FilterPanel` renders from this list rather than from a
 *  second copy of it. It is the list of SUGGESTIONS, not the list of legal
 *  values — validation is a range (`isUsableLastUpdate`), and the panel
 *  adds any value outside this list to its own options so that a filter it
 *  is sending is one the reader can see and clear. */
export const LAST_UPDATE_OPTIONS = [
  { value: "1", label: "the last year" },
  { value: "2", label: "the last 2 years" },
  { value: "3", label: "the last 3 years" },
  { value: "5", label: "the last 5 years" },
] as const;

/** Whether an "updated within N years" value is one the backend can use.
 *
 *  A RANGE, not membership in the panel's four options. The backend takes
 *  any positive count (`by_date_since` → `cast_str_to_int` → `timedelta`),
 *  so a `lastUpdate` of "4" or "10" from a host's `initialFilters` — or
 *  written into storage by another client — is perfectly good, and
 *  refusing it would not merely ignore the filter: a stored value this
 *  remote refuses is nulled out of the PROMOP row by the next save.
 *
 *  Two values it does exclude, both of which the backend mishandles:
 *
 *    - `"0"` passes its `isdigit` check and then fails
 *      `if not since_in_years`, so it filters nothing at all while the
 *      panel shows a filter set.
 *    - a count large enough to take `datetime.now() - timedelta(365*n)`
 *      below year 1, which is an OverflowError and a 500 on every search
 *      until the value is cleared. That threshold is
 *      `(datetime.now() - datetime.min).days // 365` — a little over 2027
 *      today, and rising by one a year. The bound here is 2000: under it
 *      for centuries, and past anything that means something (a registry
 *      that starts in 2000 is fully covered by 30).
 */
export function isUsableLastUpdate(value: unknown): boolean {
  if (typeof value !== "string" || !/^\d+$/.test(value)) return false;
  // No length bound beside this: a string of ten thousand digits is
  // `Infinity` here and fails the comparison, so a second guard would be
  // one no test could tell from the first.
  const years = Number(value);
  return years >= 1 && years <= 2000;
}

/** How each stored field is checked on the way back in. `distance`,
 *  `distanceUnits` and `lastUpdate` are handled separately below. */
const STORED_STRING_FIELDS: readonly string[] = PANEL_FIELDS.filter(
  (f) => f !== "distance" && f !== "validatedOnly" && f !== "lastUpdate",
);

/** What to trust from storage.
 *
 *  The saved set is opaque JSON wherever it is kept: PROMOP validates that
 *  it is an object and nothing more, and the `localStorage` fallback is a
 *  file on a disk anyone with the browser can edit. This remote is not its
 *  only writer either — another client, or an older build of this one, may
 *  have put something there that no longer means what it meant.
 *
 *  So a stored value is treated as a claim rather than as a `FilterState`.
 *  Unknown KEYS are dropped, as are values of the wrong TYPE — which would
 *  otherwise be handed to a control expecting a string and rendered as
 *  `[object Object]`. `type` and `sort` go with them: they are not filters,
 *  and `type: "all"` moves the server to the admin branch, which skips the
 *  eligibility filter and several study preferences with it (#424).
 *
 *  What this does NOT catch is a value of the right type that is not one of
 *  the options — `phase: "PHASE7"`, or a `trialType` from another disease.
 *  Membership cannot be decided here: the options are per disease and
 *  arrive asynchronously from `/form-settings/`, which is #444. What the
 *  panel does instead is show such a value rather than collapse to "Any",
 *  so a filter that is narrowing the list is one the reader can see and
 *  clear.
 *
 *  Length is deliberately NOT checked. A cap here refuses a value this very
 *  client wrote (a pasted trial title runs past 200 characters easily), and
 *  refusing it is not inert: `adapterPreferences` nulls every key absent
 *  from the next payload, so the filter is deleted from the row — while on
 *  `localStorage` the same value survives. The server is where a request
 *  too large to serve gets refused.
 */
export function sanitizeStoredFilters(raw: unknown): FilterState {
  // `== null` only: a string or a number has none of these keys, so it
  // falls out of the loop as `{}` anyway. A `typeof` check beside it would
  // be a second mechanism no test could tell from the first.
  if (raw == null) return {};
  const input = raw as Record<string, unknown>;
  const out: FilterState = {};

  for (const field of STORED_STRING_FIELDS) {
    const value = input[field];
    if (typeof value !== "string" || value === "") continue;
    (out as Record<string, unknown>)[field] = value;
  }

  // A count of years the backend can actually use — see
  // `isUsableLastUpdate`. What this drops is an ISO date, which is what CB
  // writes into this field (#429) into the same PROMOP row this remote
  // reads: `by_date_since` gets nothing out of it, so it filters nothing
  // while the control shows "Any".
  if (isUsableLastUpdate(input.lastUpdate)) {
    out.lastUpdate = input.lastUpdate as string;
  }

  if (typeof input.validatedOnly === "boolean") {
    // `false` is kept, not treated as absent: the host may have seeded
    // `true`, and the reader unchecking it is a choice worth storing. It
    // does not count on the badge either way — `isInactive` reads `false`
    // as empty.
    out.validatedOnly = input.validatedOnly;
  }

  // The unit stands on its own. `userOwnedFilters` stores it without a
  // distance on purpose — the reader switching the host's 50 km to 50 miles
  // has changed nothing but the unit — so dropping it here would silently
  // put them back on kilometres at the next mount, which is a materially
  // different search.
  const unit = input.distanceUnits;
  const readableUnit = unit === "km" || unit === "miles";
  if (readableUnit) out.distanceUnits = unit;

  // Only a radius the backend will actually honour. Zero applies no limit
  // and a negative one becomes a negative geospatial radius — an empty
  // result set for a reason nothing on screen explains.
  //
  // And only with a unit that can be read, or none at all. A radius whose
  // unit is garbled is not a radius: keeping the number looks harmless and
  // is not, because `by_distance` compares against `miles` and treats
  // everything else as kilometres, so a stored 50 MILES would quietly
  // become 50 km — from a value we had just admitted we could not parse.
  if (isActiveDistance(input.distance) && (unit === undefined || readableUnit)) {
    out.distance = input.distance as number;
  }

  return out;
}
