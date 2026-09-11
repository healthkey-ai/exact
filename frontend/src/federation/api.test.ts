import { describe, expect, it, vi } from "vitest";
import type { AxiosInstance } from "axios";

import {
  fetchTrialDetail,
  fetchTrials,
  filterStateToParams,
  hasInlinePatient,
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
      trialPurpose: "treatment",
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
      filterStateToParams({ phase: "PHASE3", lastUpdate: "2026-01-01" }),
    ).toEqual({ phase: "PHASE3", lastUpdate: "2026-01-01" });
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
