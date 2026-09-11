import { describe, expect, it } from "vitest";

import {
  DISTANCE_UNITS,
  PERSISTED_FIELDS,
  baselineFilters,
  countActiveFilters,
  countryFor,
  filtersToStore,
  hasActiveFilters,
  isActiveDistance,
  sanitizeStoredFilters,
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

describe("what is saved", () => {
  it("saves only what the panel can change", () => {
    // `type` and `sort` belong to other controls; `country` is derived from
    // the patient; `region`/`studyType`/`validatedOnly` can only come from
    // the host, and saving them would pin its scope into the patient's own
    // preferences, outliving a host that stopped sending it.
    expect(
      filtersToStore({
        searchTitle: "myeloma",
        type: "potential",
        sort: "distance",
        country: "US",
        region: "EU",
        studyType: "INTERVENTIONAL",
        validatedOnly: true,
      }),
    ).toEqual({ searchTitle: "myeloma" });
  });

  it("leaves out what is not set, so the stored object says only what was", () => {
    expect(filtersToStore({ searchTitle: "", sponsor: undefined })).toEqual({});
  });

  it("keeps a distance only when the backend would honour it", () => {
    // Zero applies no limit and a negative one becomes a negative radius —
    // an empty result set for a reason nothing on screen explains.
    expect(filtersToStore({ distance: 50, distanceUnits: "km" })).toEqual({
      distance: 50,
      distanceUnits: "km",
    });
    expect(filtersToStore({ distance: 0, distanceUnits: "km" })).toEqual({});
    expect(filtersToStore({ distance: -5, distanceUnits: "km" })).toEqual({});
  });

  it("does not save a unit for a distance that is not there", () => {
    expect(filtersToStore({ distanceUnits: "miles" })).toEqual({});
  });

  it("truncates rather than storing something no request could carry", () => {
    const stored = filtersToStore({ searchTitle: "x".repeat(5000) });
    expect((stored.searchTitle as string).length).toBe(200);
  });
});

describe("what is trusted on the way back", () => {
  it("keeps the fields it knows", () => {
    expect(
      sanitizeStoredFilters({ searchTitle: "myeloma", phase: "PHASE3" }),
    ).toEqual({ searchTitle: "myeloma", phase: "PHASE3" });
  });

  it("drops a key it does not know", () => {
    // The payload is opaque JSON on the server and this remote is not its
    // only writer. An unknown key riding into the request is a filter the
    // reader cannot see and cannot turn off.
    expect(sanitizeStoredFilters({ searchTitle: "a", favouriteColour: "blue" })).toEqual(
      { searchTitle: "a" },
    );
  });

  it("refuses a tab or a sort order smuggled in as a filter", () => {
    // `type: "all"` is not a filter: it switches the server to the admin
    // corpus, which skips the eligibility filter and several study
    // preferences with it (#424), and suppresses the tab counts.
    expect(sanitizeStoredFilters({ type: "all", sort: "distance" })).toEqual({});
  });

  it("drops a value of the wrong type rather than handing it to a control", () => {
    expect(
      sanitizeStoredFilters({ searchTitle: { toString: 1 }, phase: 42, sponsor: null }),
    ).toEqual({});
  });

  it("drops a distance that is not a usable radius", () => {
    expect(sanitizeStoredFilters({ distance: "50" })).toEqual({});
    expect(sanitizeStoredFilters({ distance: 0 })).toEqual({});
    expect(sanitizeStoredFilters({ distance: Number.NaN })).toEqual({});
  });

  it("drops a unit that qualifies nothing", () => {
    expect(sanitizeStoredFilters({ distanceUnits: "km" })).toEqual({});
    expect(sanitizeStoredFilters({ distance: 10, distanceUnits: "furlongs" })).toEqual({
      distance: 10,
    });
  });

  it("reads a payload that is not an object at all as no filters", () => {
    // PROMOP now refuses these on the way in, but rows written before that
    // guard — or by another client against an older build — are still there.
    for (const junk of [null, undefined, [], "phase=3", 42]) {
      expect(sanitizeStoredFilters(junk)).toEqual({});
    }
  });

  it("refuses a string longer than it would store", () => {
    expect(sanitizeStoredFilters({ searchTitle: "x".repeat(201) })).toEqual({});
  });
});

describe("which fields are persisted at all", () => {
  it("is exactly this list", () => {
    // Pinned by name. Five of them were carried by no other test: a field
    // could drop out of the list, stop being saved, and nothing would say
    // so — the reader's filter would simply not be there next time.
    expect([...PERSISTED_FIELDS]).toEqual([
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
    ]);
  });

  it("saves every one of them", () => {
    const everything = {
      searchTitle: "a",
      searchTreatment: "b",
      sponsor: "c",
      trialPurpose: "treatment",
      recruitmentStatus: "RECRUITING",
      phase: "PHASE3",
      register: "NCT",
      lastUpdate: "2",
      distance: 10,
      distanceUnits: "km" as const,
    };
    expect(filtersToStore(everything)).toEqual(everything);
    expect(sanitizeStoredFilters(everything)).toEqual(everything);
  });

  it("does not save the trial type", () => {
    // It is scoped to the disease: a type chosen under one has no matching
    // option under another, and `by_trial_type` has no leniency for a value
    // that is not there — an empty list from a control rendering blank
    // (#437).
    expect(filtersToStore({ trialType: "drug" })).toEqual({});
    expect(sanitizeStoredFilters({ trialType: "drug" })).toEqual({});
  });

  it("survives the round trip at the length limit", () => {
    // One side truncates and the other drops what is too long; if they ever
    // disagree by one character, a saved filter comes back as nothing.
    const stored = filtersToStore({ searchTitle: "x".repeat(5000) });
    expect(sanitizeStoredFilters(stored)).toEqual(stored);
  });
});

describe("what the host asked for is not the patient's own", () => {
  it("leaves a host filter out of the save", () => {
    // The panel is fed the effective filters, so a host's initialFilters
    // ride along in every field they touched. Saved, they outrank the host
    // on the next mount and outlive a host that stopped sending them.
    expect(
      filtersToStore(
        { searchTitle: "mm", recruitmentStatus: "RECRUITING", phase: "PHASE2" },
        { recruitmentStatus: "RECRUITING", phase: "PHASE2" },
      ),
    ).toEqual({ searchTitle: "mm" });
  });

  it("keeps a stored value that the host happens to agree with", () => {
    // The two can agree by coincidence. Since a save replaces the stored
    // set whole, dropping the field on that coincidence DELETES the
    // patient's preference — and it surfaces the day the host stops
    // sending it, which is the day it was supposed to still be there.
    expect(
      filtersToStore(
        { phase: "PHASE3", searchTitle: "mm" },
        { phase: "PHASE3" },
        { phase: "PHASE3" },
      ),
    ).toEqual({ phase: "PHASE3", searchTitle: "mm" });
  });

  it("keeps a value the reader changed away from the host's", () => {
    expect(
      filtersToStore({ phase: "PHASE3" }, { phase: "PHASE2" }),
    ).toEqual({ phase: "PHASE3" });
  });

  it("stores nothing when the reader changed nothing", () => {
    const host = { sponsor: "Acme", distance: 50, distanceUnits: "km" as const };
    expect(filtersToStore(host, host)).toEqual({});
  });
});

describe("a stored radius keeps its unit", () => {
  it("even when the unit is the one the host asked for", () => {
    // Subtracted as the host's, a saved 50 MILES comes back as 50 km the
    // day the host stops sending the unit — a different set of trials, and
    // nothing on screen to say why.
    expect(
      filtersToStore({ distance: 50, distanceUnits: "miles" }, { distanceUnits: "miles" }),
    ).toEqual({ distance: 50, distanceUnits: "miles" });
  });

  it("and still stores no unit without a distance", () => {
    expect(filtersToStore({ distanceUnits: "miles" })).toEqual({});
  });
});

describe("an empty string is not a filter", () => {
  it("is not stored", () => {
    expect(filtersToStore({ searchTitle: "" })).toEqual({});
  });

  it("is not read back either", () => {
    // A foreign writer's `{searchTitle: ""}` would otherwise ride into the
    // panel and count on the badge as a filter that narrows nothing.
    expect(sanitizeStoredFilters({ searchTitle: "" })).toEqual({});
  });
});
