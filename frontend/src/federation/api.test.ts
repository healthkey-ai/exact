import { describe, expect, it, vi } from "vitest";
import type { AxiosInstance } from "axios";

import {
  fetchTrialDetail,
  fetchTrials,
  filterStateToParams,
  hasInlinePatient,
  inlinePatientId,
  patientHandleOf,
} from "./api";
import type { FilterState } from "./types";

describe("filterStateToParams", () => {
  it("returns an empty object for no/empty filters", () => {
    expect(filterStateToParams()).toEqual({});
    expect(filterStateToParams({})).toEqual({});
  });

  // Locks the camelCase -> query-param mapping against backend drift. Most
  // keys are consumed by `study_preferences_from_query_params`
  // (trials/services/study_preferences.py); `type` and `sort` are read by the
  // trials view itself. If the backend renames a param, update this test in
  // the same change.
  it("maps every filter key to the param name the backend expects", () => {
    const filters: FilterState = {
      recruitmentStatus: "recruiting",
      country: "US",
      region: "NY",
      trialType: "interventional",
      trialPurpose: ["treatment"],
      studyType: "phase2",
      distance: 50,
      distanceUnits: "km",
      validatedOnly: true,
      sponsor: "BioPharm",
      register: "ctgov",
      searchTitle: "myeloma",
      type: "eligible",
      sort: "goodnessScore",
    };
    expect(filterStateToParams(filters)).toEqual({
      recruitmentStatus: "recruiting",
      country: "US",
      region: "NY",
      trialType: "interventional",
      trialPurpose: "treatment",
      studyType: "phase2",
      distance: "50",
      distanceUnits: "km",
      validatedOnly: "true",
      sponsor: "BioPharm",
      register: "ctgov",
      searchTitle: "myeloma",
      type: "eligible",
      sort: "goodnessScore",
    });
  });

  it("stringifies a real distance and omits a falsy validatedOnly", () => {
    expect(filterStateToParams({ distance: 50 })).toEqual({ distance: "50" });
    expect(filterStateToParams({ validatedOnly: false })).toEqual({});
  });

  it("omits a zero distance rather than sending a parameter that does nothing", () => {
    // This used to assert `{distance: "0"}`. The backend gates on
    // `if study_info.distance:`, so zero applies no radius at all — putting
    // it on the wire made the request look filtered when it was not, and
    // the panel's badge counted it.
    expect(filterStateToParams({ distance: 0 })).toEqual({});
  });

  it("omits keys that are absent", () => {
    expect(filterStateToParams({ country: "DE" })).toEqual({ country: "DE" });
  });
});

function fakeClient() {
  const post = vi.fn().mockResolvedValue({ data: { count: 0, next: null, previous: null, results: [] } });
  const get = vi.fn().mockResolvedValue({ data: { count: 0, next: null, previous: null, results: [] } });
  return { post, get } as unknown as AxiosInstance & {
    post: ReturnType<typeof vi.fn>;
    get: ReturnType<typeof vi.fn>;
  };
}

describe("fetchTrials routing", () => {
  it("POSTs to /trials/search/match/ with patient_info when an inline payload is given", async () => {
    const apiClient = fakeClient();
    await fetchTrials({
      apiClient,
      patientInfo: { disease: "MM" },
      filters: { country: "US" },
    });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/trials/search/match/",
      { patient_info: { disease: "MM" } },
      { params: { country: "US" } },
    );
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  it("treats an empty patientInfo object as no inline payload and GETs instead", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient, patientInfo: {}, personId: 7 });
    expect(apiClient.post).not.toHaveBeenCalled();
    expect(apiClient.get).toHaveBeenCalledWith("/trials/search/", {
      params: { person_id: "7" },
    });
  });

  it("GETs /trials/search/?person_id= for the server-side resolver path", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient, personId: 42, filters: { sort: "matchScore" } });
    expect(apiClient.get).toHaveBeenCalledWith("/trials/search/", {
      params: { sort: "matchScore", person_id: "42" },
    });
  });

  it("GETs /trials/search/ with no person_id when neither patient context is given", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient });
    expect(apiClient.get).toHaveBeenCalledWith("/trials/search/", { params: {} });
  });
});

describe("fetchTrialDetail routing", () => {
  it("POSTs to /trials/{id}/match/ with patient_info for the inline path", async () => {
    const apiClient = fakeClient();
    await fetchTrialDetail({ apiClient, trialId: 42, patientInfo: { disease: "MM" } });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/trials/42/match/",
      { patient_info: { disease: "MM" } },
      { params: {} },
    );
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  it("forwards study-preference filters as params (so detail agrees with the list)", async () => {
    const apiClient = fakeClient();
    await fetchTrialDetail({
      apiClient,
      trialId: 42,
      patientInfo: { disease: "MM" },
      filters: { recruitmentStatus: "RECRUITING", distanceUnits: "miles" },
    });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/trials/42/match/",
      { patient_info: { disease: "MM" } },
      { params: { recruitmentStatus: "RECRUITING", distanceUnits: "miles" } },
    );
  });

  it("GETs /trials/{id}/?person_id= for the server-side resolver path", async () => {
    const apiClient = fakeClient();
    await fetchTrialDetail({ apiClient, trialId: 42, personId: 7 });
    expect(apiClient.post).not.toHaveBeenCalled();
    expect(apiClient.get).toHaveBeenCalledWith("/trials/42/", {
      params: { person_id: "7" },
    });
  });

  it("treats an empty patientInfo object as no inline payload", async () => {
    const apiClient = fakeClient();
    await fetchTrialDetail({ apiClient, trialId: 9, patientInfo: {} });
    expect(apiClient.post).not.toHaveBeenCalled();
    expect(apiClient.get).toHaveBeenCalledWith("/trials/9/", { params: {} });
  });
});

describe("fetchTrials — the search path", () => {
  // The list used to go to `list` (`/trials/` and its POST alias
  // `/trials/match/`), where `?sort=` is ignored and there are no tab
  // counts. These lock the move to `search` so a revert would fail loudly
  // rather than silently returning an unsorted page.
  it("sends sort and page through to the search endpoint", async () => {
    const apiClient = fakeClient();
    await fetchTrials({
      apiClient,
      patientInfo: { disease: "multiple myeloma" },
      filters: { sort: "distance" },
      page: 3,
      limit: 10,
    });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/trials/search/match/",
      { patient_info: { disease: "multiple myeloma" } },
      { params: { sort: "distance", page: "3", limit: "10" } },
    );
  });

  it("omits page=1, which is the server's default", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient, personId: 9001, page: 1, limit: 10 });
    expect(apiClient.get).toHaveBeenCalledWith("/trials/search/", {
      params: { person_id: "9001", limit: "10" },
    });
  });

  it("maps the phase and lastUpdate filters the panel will send", () => {
    expect(
      filterStateToParams({ phase: "PHASE3", lastUpdate: "2" }),
    ).toEqual({ phase: "PHASE3", lastUpdate: "2" });
  });

  it("sends no lastUpdate the backend would mishandle", () => {
    // This used to forward whatever it was given. `"0"` the server reads as
    // no limit at all; a large number overflows `timedelta` into a 500 on
    // every search — which is what the 2000 cap is for, and why `2001` is
    // refused as a COUNT rather than read as a calendar year. A host's
    // `initialFilters` reaches here without passing through storage, so the
    // check has to be at the wire as well — which is what `distance` already
    // does.
    for (const junk of ["0", "3000", "2001", "2026-13-45"]) {
      expect(filterStateToParams({ lastUpdate: junk })).toEqual({});
    }
    // An ISO date now goes through: since #429 the server reads it as "on or
    // after that day", and it is the spelling CB's own panel writes.
    expect(filterStateToParams({ lastUpdate: "2026-01-01" })).toEqual({
      lastUpdate: "2026-01-01",
    });
    // …while a count the backend handles goes through, even one the panel
    // does not offer: a host may legitimately ask for four years.
    expect(filterStateToParams({ lastUpdate: "4" })).toEqual({ lastUpdate: "4" });
  });
});

describe("hasInlinePatient", () => {
  // The request and the component both have to answer "which prop is the
  // patient?" the same way. They did not, and a host updating the inline
  // payload while keeping a person id carried the previous patient's
  // filters into the new patient's search — hence one exported function.
  it("is true for a payload with fields", () => {
    expect(hasInlinePatient({ disease: "multiple myeloma" })).toBe(true);
  });

  it("is false for nothing, null, or an empty object", () => {
    // `{}` carries no patient, and the request falls through to the
    // person_id path for it — so the identity must fall through too.
    expect(hasInlinePatient(undefined)).toBe(false);
    expect(hasInlinePatient(null)).toBe(false);
    expect(hasInlinePatient({})).toBe(false);
  });

  it("agrees with the endpoint fetchTrials actually calls", async () => {
    // Pins the two together: if the precedence changes in one place this
    // fails rather than silently drifting again.
    for (const payload of [undefined, null, {}, { disease: "mm" }]) {
      const apiClient = fakeClient();
      await fetchTrials({ apiClient, patientInfo: payload, personId: 9001 });
      expect(apiClient.post.mock.calls.length > 0).toBe(hasInlinePatient(payload));
      expect(apiClient.get.mock.calls.length > 0).toBe(!hasInlinePatient(payload));
    }
  });
});

describe("filterStateToParams — distance", () => {
  it("omits a negative distance a host could pass in", () => {
    // Truthiness is not enough: `-1` is truthy, reaches the backend's
    // distance branch, and becomes a negative radius.
    expect(filterStateToParams({ distance: -1 })).toEqual({});
  });
});

describe("fetchTrials — trial_ids", () => {
  it("sends an empty list as an empty list, not as no filter", () => {
    // The case the whole feature turns on: `[]` means "my bookmarks, of
    // which there are none". Collapsing it into "no filter" answers an
    // empty Favorites tab with every trial in the registry.
    const apiClient = fakeClient();
    return fetchTrials({
      apiClient,
      patientInfo: { disease: "mm" },
      trialIds: [],
    }).then(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        "/trials/search/match/",
        { patient_info: { disease: "mm" }, trial_ids: [] },
        { params: {} },
      );
    });
  });

  it("omits the key entirely when there is no id filter", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient, patientInfo: { disease: "mm" } });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/trials/search/match/",
      { patient_info: { disease: "mm" } },
      { params: {} },
    );
  });

  it("posts on the person_id path too, because ids only travel in a body", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient, personId: 9001, trialIds: ["7"] });
    expect(apiClient.get).not.toHaveBeenCalled();
    expect(apiClient.post).toHaveBeenCalledWith(
      "/trials/search/match/",
      { trial_ids: ["7"] },
      { params: { person_id: "9001" } },
    );
  });
});

describe("trialPurpose on the wire (#428)", () => {
  it("sends several codes as one comma-separated param", () => {
    // Not a repeated param: axios's default serializer spells an array
    // `trialPurpose[]=a&trialPurpose[]=b`, and Django's
    // `getlist('trialPurpose')` does not see the bracketed key. The backend's
    // `_str_list` accepts the comma form for exactly this reason.
    expect(
      filterStateToParams({ trialPurpose: ["treatment", "prevention"] }),
    ).toEqual({ trialPurpose: "treatment,prevention" });
  });

  it("sends a single code unchanged, as the pre-#4663 contract did", () => {
    expect(filterStateToParams({ trialPurpose: ["treatment"] })).toEqual({
      trialPurpose: "treatment",
    });
  });

  it("omits the param entirely when nothing is selected", () => {
    // `[].join(",")` is `""`, which would put `?trialPurpose=` on the wire.
    // `_str_list` drops blanks so the result would be the same, but the param
    // would show up in the URL as a filter the reader did not set.
    expect(filterStateToParams({ trialPurpose: [] })).toEqual({});
    expect(filterStateToParams({})).toEqual({});
  });
});

describe("trialPurpose from a build that spelled it a string", () => {
  it("does not throw, and filters on the code it names", () => {
    // The second lock: the state is normalized on the way in, but a host can
    // reach `fetchTrials` by paths that never pass through the panel. Before
    // this, `.length` was truthy and `.join` threw — taking the whole trial
    // list down on every mount rather than dropping one filter.
    expect(
      filterStateToParams({ trialPurpose: "treatment" as never }),
    ).toEqual({ trialPurpose: "treatment" });
  });

  it("an empty string is no param at all", () => {
    expect(filterStateToParams({ trialPurpose: "" as never })).toEqual({});
  });
});

describe("patientHandleOf", () => {
  // This string is the key the write queue, the writable-fields descriptor
  // and the open trial all hang on. Two patients sharing one means an edit
  // queued for the first is sent through the second's adapter — a lab value
  // in the wrong chart — so the two properties below are load-bearing, and
  // the component suite cannot see either of them.

  it("cannot be spelled by another pair, whatever is in the ids", () => {
    // Joined on a separator, `("a|b", "c")` and `("a", "b|c")` are the same
    // string. Hashed as a pair, they are not.
    expect(patientHandleOf({ id: "c" }, "a|b")).not.toBe(
      patientHandleOf({ id: "b|c" }, "a"),
    );
  });

  it("does not move when a host rebuilds an id-less payload in another order", () => {
    // Same patient, same fields, different insertion order — which is what a
    // host re-reading a profile produces. `JSON.stringify` calls that a new
    // patient: the open trial closes and the descriptor is refetched.
    expect(patientHandleOf({ a: 1, b: 2 }, undefined)).toBe(
      patientHandleOf({ b: 2, a: 1 }, undefined),
    );
  });

  it("tells a number from a string, and either from nothing", () => {
    // Concatenation coerced them together. Keeping them apart is the safe
    // direction — it resets where it need not, rather than treating two
    // patients as one — but it is a contract worth stating: a host that
    // flips `personId` between 9009 and "9009" for one patient is telling
    // this remote the patient changed.
    expect(patientHandleOf(null, 9009)).not.toBe(patientHandleOf(null, "9009"));
    expect(patientHandleOf(null, "")).not.toBe(patientHandleOf(null, undefined));
  });
});

describe("inlinePatientId", () => {
  it("takes the first field that actually holds an id", () => {
    expect(inlinePatientId({ person_id: 9009 })).toBe("9009");
    expect(inlinePatientId({ external_id: "abc" })).toBe("abc");
    expect(inlinePatientId({ patient_id: "7" })).toBe("7");
  });

  it("steps over a field that is present but empty", () => {
    // A column that exists and is blank. Taken as an id on position alone,
    // every patient carrying it hashes to the same handle.
    expect(inlinePatientId({ person_id: "", id: 9009 })).toBe("9009");
    expect(inlinePatientId({ person_id: "   ", id: 9009 })).toBe("9009");
    expect(inlinePatientId({ person_id: Number.NaN, id: 9009 })).toBe("9009");
  });

  it("answers null when nothing names the patient", () => {
    expect(inlinePatientId({ disease: "multiple myeloma" })).toBeNull();
    expect(inlinePatientId(null)).toBeNull();
    // `0` is an id; `false` is not.
    expect(inlinePatientId({ id: 0 })).toBe("0");
    expect(inlinePatientId({ id: false as never })).toBeNull();
  });

  it("does not carry surrounding whitespace into the handle", () => {
    expect(inlinePatientId({ person_id: " 9009 " })).toBe("9009");
  });
});
