import { describe, expect, it } from "vitest";

import {
  DISTANCE_UNITS,
  baselineFilters,
  countActiveFilters,
  countryFor,
  hasActiveFilters,
  isActiveDistance,
} from "./filters";

describe("baselineFilters", () => {
  it("carries the patient's country as the default", () => {
    // Reset must not clear it: the country is seeded from the profile, and
    // clearing would silently widen the search to every country in the
    // registry rather than restoring the default.
    expect(baselineFilters("US")).toEqual({ country: "US" });
  });

  it("is empty for a patient with no country", () => {
    expect(baselineFilters(undefined)).toEqual({});
  });
});

describe("countActiveFilters", () => {
  const base = baselineFilters("US");

  it("counts nothing when the panel matches the baseline", () => {
    expect(countActiveFilters({ country: "US" }, base)).toBe(0);
    expect(countActiveFilters({}, baselineFilters(undefined))).toBe(0);
  });

  it("does not count the patient's own country as a filter", () => {
    // The badge is meant to say "you narrowed this". A country the reader
    // never chose, that merely reflects where they are, is not that.
    expect(countActiveFilters({ country: "US", sponsor: "BioPharm" }, base)).toBe(1);
  });

  it("counts a country the reader chose over the seeded one", () => {
    expect(countActiveFilters({ country: "DE" }, base)).toBe(1);
  });

  it("counts each changed field once", () => {
    expect(
      countActiveFilters(
        {
          country: "US",
          searchTitle: "myeloma",
          phase: "PHASE3",
          validatedOnly: true,
          distance: 50,
        },
        base,
      ),
    ).toBe(4);
  });

  it("treats empty string, null and false as unset", () => {
    // A cleared text input hands back "" before the caller normalises it,
    // and an unticked checkbox is `false` — neither is a filter.
    expect(
      countActiveFilters(
        { country: "US", searchTitle: "", sponsor: undefined, validatedOnly: false },
        base,
      ),
    ).toBe(0);
  });

  it("ignores the tab and the sort control", () => {
    // Those live outside the panel; counting them would tick the badge up
    // when the reader switches tab, which they did not experience as
    // filtering.
    expect(
      countActiveFilters({ country: "US", type: "potential", sort: "distance" }, base),
    ).toBe(0);
  });

  it("counts neither a zero nor a negative distance", () => {
    // The backend gates on `if study_info.distance:`, so zero applies no
    // limit. The control cannot produce one, but a host can pass it through
    // `initialFilters`, and the badge must not claim a narrowing that is
    // not running.
    expect(countActiveFilters({ country: "US", distance: 0 }, base)).toBe(0);
    expect(countActiveFilters({ country: "US", distance: -1 }, base)).toBe(0);
    expect(countActiveFilters({ country: "US", distance: 25 }, base)).toBe(1);
  });

  it("ignores distance units, which cannot filter on their own", () => {
    expect(countActiveFilters({ country: "US", distanceUnits: "miles" }, base)).toBe(0);
    expect(
      countActiveFilters({ country: "US", distance: 50, distanceUnits: "miles" }, base),
    ).toBe(1);
  });
});

describe("hasActiveFilters", () => {
  it("is false at the baseline and true once something changes", () => {
    const base = baselineFilters("US");
    expect(hasActiveFilters({ country: "US" }, base)).toBe(false);
    expect(hasActiveFilters({ country: "US", register: "clinicaltrials.gov" }, base)).toBe(
      true,
    );
  });
});

describe("DISTANCE_UNITS", () => {
  it("sends the values the backend compares against", () => {
    // `by_distance` checks for `miles` and treats anything else as km. CB
    // sends `kilometers`, which filters correctly but is echoed back into
    // the response and rendered as "743 kilometers".
    expect(DISTANCE_UNITS.map((u) => u.value)).toEqual(["miles", "km"]);
  });
});

describe("baselineFilters with host-supplied initialFilters", () => {
  it("keeps the host's filters, so Reset does not discard them", () => {
    // A host that mounts the remote already scoped to a register means that
    // scope to survive the button; Reset clearing it would silently widen
    // the search past what the host asked for.
    expect(baselineFilters("US", { register: "clinicaltrials.gov" })).toEqual({
      register: "clinicaltrials.gov",
      country: "US",
    });
  });

  it("lets the patient's own country win over a host default", () => {
    expect(baselineFilters("DE", { country: "US" })).toEqual({ country: "DE" });
  });

  it("keeps the host's country when the patient has none", () => {
    expect(baselineFilters(undefined, { country: "US" })).toEqual({ country: "US" });
  });
});

describe("countActiveFilters over fields with no control", () => {
  it("counts region and studyType, which only a host can set", () => {
    // They have no control in the panel but api.ts still sends them. Left
    // out of the count, the badge would read 0 while a filter was running,
    // and Reset would leave it in place.
    const base = baselineFilters("US");
    expect(countActiveFilters({ country: "US", region: "NY" }, base)).toBe(1);
    expect(countActiveFilters({ country: "US", studyType: "INTERVENTIONAL" }, base)).toBe(1);
  });
});

describe("countryFor", () => {
  // The component seeds the filter with this and the baseline computes the
  // badge from it. Written separately they drifted: the seed cleared a
  // host-supplied country that the baseline kept, so the badge read
  // "Filters (1)" for a value never sent, and Reset changed the results.
  it("prefers the patient's own country", () => {
    expect(countryFor("DE", { country: "US" })).toBe("DE");
  });

  it("falls back to the host's when the patient has none", () => {
    expect(countryFor(undefined, { country: "US" })).toBe("US");
  });

  it("is undefined when neither says anything", () => {
    expect(countryFor(undefined, {})).toBeUndefined();
    expect(countryFor(undefined, undefined)).toBeUndefined();
  });

  it("is what the baseline uses, so the two cannot disagree", () => {
    // "" included deliberately: `??` would have kept it here and the
    // baseline's own guard would have dropped it.
    for (const patient of [undefined, "", "DE"]) {
      for (const initial of [undefined, {}, { country: "US" }]) {
        expect(baselineFilters(patient, initial).country).toBe(
          countryFor(patient, initial),
        );
      }
    }
  });
});

describe("isActiveDistance", () => {
  it("accepts only a positive, finite radius", () => {
    expect(isActiveDistance(25)).toBe(true);
    expect(isActiveDistance(0.5)).toBe(true);
  });

  it("rejects zero, which the backend reads as no limit at all", () => {
    expect(isActiveDistance(0)).toBe(false);
  });

  it("rejects a negative radius, which is worse than none", () => {
    // `if study_info.distance:` is true for -1, so it reaches the distance
    // branch and builds a negative geospatial radius: an empty result set
    // with nothing on screen to explain it.
    expect(isActiveDistance(-1)).toBe(false);
  });

  it("rejects what is not a finite number", () => {
    expect(isActiveDistance(undefined)).toBe(false);
    expect(isActiveDistance(null)).toBe(false);
    expect(isActiveDistance("25")).toBe(false);
    expect(isActiveDistance(Number.NaN)).toBe(false);
    expect(isActiveDistance(Number.POSITIVE_INFINITY)).toBe(false);
  });
});
