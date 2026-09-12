import { describe, expect, it } from "vitest";

import {
  DISTANCE_UNITS,
  baselineFilters,
  countActiveFilters,
  countryFor,
  hasActiveFilters,
  isActiveDistance,
  sanitizeStoredFilters,
  userOwnedFilters,
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

describe("userOwnedFilters", () => {
  it("keeps only what the reader changed", () => {
    const baseline = { country: "US", recruitmentStatus: "RECRUITING" };
    const filters = { country: "US", recruitmentStatus: "RECRUITING", searchTitle: "vrd" };
    expect(userOwnedFilters(filters, baseline)).toEqual({ searchTitle: "vrd" });
  });

  it("does not save the host's mount-time scope", () => {
    // Persisting it would turn one mount's scope into a standing preference,
    // and a later mount with a different scope would lose to the saved one.
    const baseline = { country: "DE", recruitmentStatus: "NOT_YET_RECRUITING" };
    expect(userOwnedFilters({ ...baseline }, baseline)).toEqual({});
  });

  it("keeps a field the reader overrode, even back to a common value", () => {
    const baseline = { recruitmentStatus: "RECRUITING" };
    expect(userOwnedFilters({ recruitmentStatus: "COMPLETED" }, baseline)).toEqual({
      recruitmentStatus: "COMPLETED",
    });
  });

  it("marks a cleared field present-but-undefined so it can be cleared server-side", () => {
    const baseline = { searchTitle: "vrd" };
    const out = userOwnedFilters({ searchTitle: undefined }, baseline);
    expect("searchTitle" in out).toBe(true);
    expect(out.searchTitle).toBeUndefined();
  });
});

describe("userOwnedFilters — distance carries its unit", () => {
  it("saves the unit alongside a distance", () => {
    // Without it a radius chosen in miles comes back as the same number of km.
    const out = userOwnedFilters({ distance: 50, distanceUnits: "miles" }, {});
    expect(out).toEqual({ distance: 50, distanceUnits: "miles" });
  });

  it("does not save a unit with no distance behind it", () => {
    expect(userOwnedFilters({ distanceUnits: "miles" }, {})).toEqual({});
  });
});

describe("userOwnedFilters — ownership is sticky", () => {
  it("keeps a saved field whose value happens to equal the baseline", () => {
    // 50 miles against a 50-km baseline: the numbers match, the meanings do
    // not. Dropping it here would make the next unrelated edit clear the
    // reader's radius — units and all — on the server.
    const baseline = { distance: 50, distanceUnits: "km" as const };
    const next = { distance: 50, distanceUnits: "miles" as const, searchTitle: "vrd" };

    // The units go either way — the control is enabled for a host-seeded
    // distance, so switching 50 km to 50 miles has to be saved even though the
    // number never changed and `distance` itself is the host's.
    expect(userOwnedFilters(next, baseline)).toEqual({
      searchTitle: "vrd",
      distanceUnits: "miles",
    });
    // But units the reader never touched stay the host's.
    expect(userOwnedFilters({ ...next, distanceUnits: "km" }, baseline)).toEqual({
      searchTitle: "vrd",
    });
    expect(userOwnedFilters(next, baseline, new Set(["distance"]))).toEqual({
      searchTitle: "vrd",
      distance: 50,
      distanceUnits: "miles",
    });
  });
});

describe("userOwnedFilters — a units-only change", () => {
  it("is saved even though nothing else about the distance moved", () => {
    // Otherwise the reader sets a 50-mile radius against the host's 50 km,
    // nothing is persisted, and the next mount silently searches 50 km — a
    // materially different search, with no sign anything was discarded.
    const baseline = { distance: 50, distanceUnits: "km" as const };
    const next = { distance: 50, distanceUnits: "miles" as const };
    expect(userOwnedFilters(next, baseline)).toEqual({ distanceUnits: "miles" });
    // And with no distance in play there is no unit to qualify.
    expect(userOwnedFilters({ distanceUnits: "miles" as const }, {})).toEqual({});
  });
});

describe("userOwnedFilters — reverting a units-only change", () => {
  it("is saved too, or the reader cannot get back to the host's unit", () => {
    // Switching the host's km to miles stores the miles. Switching BACK
    // matches the baseline again, so without the sticky `owned` set
    // nothing is written — and on a transport that only merges, the stored
    // miles stand for ever.
    const baseline = { distance: 50, distanceUnits: "km" as const };
    const back = { distance: 50, distanceUnits: "km" as const };
    expect(userOwnedFilters(back, baseline, new Set(["distanceUnits"]))).toEqual({
      distanceUnits: "km",
    });
  });
});

describe("userOwnedFilters — clearing an owned field", () => {
  it("emits a tombstone rather than omitting the key", () => {
    // Both transports merge, so an absent key means "no opinion, keep what you
    // have". A cleared filter that is merely omitted survives storage and is
    // applied again on the next mount.
    const out = userOwnedFilters({ searchTitle: undefined }, {}, new Set(["searchTitle"]));
    expect("searchTitle" in out).toBe(true);
    expect(out.searchTitle).toBeUndefined();
    // Unowned and empty is genuinely nothing to say.
    expect(userOwnedFilters({ searchTitle: undefined }, {})).toEqual({});
  });
});

describe("userOwnedFilters — a cleared distance", () => {
  it("takes its unit with it", () => {
    // Otherwise the old unit survives on disk under a merge transport and
    // becomes the unit for the next radius the reader enters.
    const out = userOwnedFilters(
      { distance: undefined, distanceUnits: "miles" as const },
      {},
      new Set(["distance"]),
    );
    expect("distanceUnits" in out).toBe(true);
    expect(out.distanceUnits).toBeUndefined();
  });
});

describe("what the stored set is trusted to contain", () => {
  it("keeps the fields it knows", () => {
    expect(
      sanitizeStoredFilters({ sponsor: "Acme", phase: "PHASE3", validatedOnly: true }),
    ).toEqual({ sponsor: "Acme", phase: "PHASE3", validatedOnly: true });
  });

  it("keeps every field the panel can save", () => {
    // By name. Six of these were carried by no test at all: a field missing
    // from the allowlist stops surviving the round trip, and since a save
    // writes the set the reader is holding, the filter is then deleted
    // rather than merely ignored.
    const everything = {
      searchTitle: "vrd",
      searchTreatment: "len",
      sponsor: "Acme",
      trialType: "drug",
      trialPurpose: "treatment",
      recruitmentStatus: "RECRUITING",
      phase: "PHASE3",
      register: "NCT",
      lastUpdate: "2",
      country: "US",
      region: "EU",
      studyType: "INTERVENTIONAL",
      validatedOnly: true,
      distance: 50,
      distanceUnits: "km" as const,
    };
    expect(sanitizeStoredFilters(everything)).toEqual(everything);
  });

  it("drops a key it does not know", () => {
    // Asserted here rather than through the request: `fetchTrials` forwards
    // only the keys it recognises, so an unknown one riding in the saved
    // set is invisible at that level — and would stay invisible right up
    // until someone adds the matching parameter.
    expect(sanitizeStoredFilters({ sponsor: "Acme", favouriteColour: "blue" })).toEqual({
      sponsor: "Acme",
    });
  });

  it("refuses a tab or a sort order smuggled in as a filter", () => {
    expect(sanitizeStoredFilters({ type: "all", sort: "distance" })).toEqual({});
  });

  it("drops a value of the wrong type", () => {
    expect(
      sanitizeStoredFilters({ phase: 42, sponsor: { not: "a string" }, searchTitle: null }),
    ).toEqual({});
  });

  it("drops an empty string, which narrows nothing", () => {
    expect(sanitizeStoredFilters({ sponsor: "" })).toEqual({});
  });

  it("does not refuse a long value this client itself can write", () => {
    // A pasted trial title runs past 200 characters easily, and refusing it
    // here is not inert: `adapterPreferences` nulls every key absent from
    // the next payload, so the filter would be DELETED from the row — while
    // the same value survives on localStorage. Length is the server's
    // business.
    const long = "x".repeat(246);
    expect(sanitizeStoredFilters({ searchTitle: long })).toEqual({ searchTitle: long });
  });

  it("keeps a lastUpdate the backend can use, and only that", () => {
    // A range, not the panel's four options: another client — or a host —
    // may legitimately say 4 or 10 years, and refusing a stored value does
    // not merely ignore it, it gets nulled out of the row by the next save.
    for (const good of ["1", "2", "4", "10", "100", "2000"]) {
      expect(sanitizeStoredFilters({ lastUpdate: good })).toEqual({
        lastUpdate: good,
      });
    }
    // An ISO date is what CB writes into this field (#429) and the backend
    // gets nothing out of it; `"0"` it reads as no limit at all; a few
    // thousand years takes its date arithmetic below year 1 and 500s.
    for (const junk of ["2020-01-01", "0", "00", "2001", "3000", ""]) {
      expect(sanitizeStoredFilters({ lastUpdate: junk })).toEqual({});
    }
  });

  it("keeps only a radius the backend will honour", () => {
    expect(sanitizeStoredFilters({ distance: 50 })).toEqual({ distance: 50 });
    expect(sanitizeStoredFilters({ distance: 0 })).toEqual({});
    expect(sanitizeStoredFilters({ distance: -5 })).toEqual({});
    expect(sanitizeStoredFilters({ distance: "50" })).toEqual({});
    expect(sanitizeStoredFilters({ distance: Number.NaN })).toEqual({});
  });

  it("drops the radius when its unit cannot be read", () => {
    expect(sanitizeStoredFilters({ distance: 50, distanceUnits: "furlongs" })).toEqual({});
    expect(sanitizeStoredFilters({ distance: 50, distanceUnits: "miles" })).toEqual({
      distance: 50,
      distanceUnits: "miles",
    });
  });

  it("keeps a unit stored without a distance", () => {
    // `userOwnedFilters` stores exactly that when the reader switches the
    // host's 50 km to 50 miles: the number never moved, so only the unit is
    // theirs. Dropped here, the next mount silently searches kilometres
    // again — see "userOwnedFilters — a units-only change".
    expect(sanitizeStoredFilters({ distanceUnits: "miles" })).toEqual({
      distanceUnits: "miles",
    });
  });

  it("reads a payload that is not an object as no filters", () => {
    for (const junk of [null, undefined, "phase=3", 42]) {
      expect(sanitizeStoredFilters(junk)).toEqual({});
    }
  });

  it("keeps validatedOnly either way, but only as a boolean", () => {
    // `false` is kept because the host may have seeded `true` and the
    // reader unchecking it is a choice worth storing — the same shape of
    // action as switching the host's kilometres to miles.
    expect(sanitizeStoredFilters({ validatedOnly: false })).toEqual({
      validatedOnly: false,
    });
    expect(sanitizeStoredFilters({ validatedOnly: "yes" })).toEqual({});
  });
});
