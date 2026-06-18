import { describe, expect, it, vi } from "vitest";
import type { AxiosInstance } from "axios";

import { fetchTrials, filterStateToParams } from "./api";
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

  it("stringifies distance, including 0, and omits a falsy validatedOnly", () => {
    expect(filterStateToParams({ distance: 0 })).toEqual({ distance: "0" });
    expect(filterStateToParams({ validatedOnly: false })).toEqual({});
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
  it("POSTs to /trials/match/ with patient_info when an inline payload is given", async () => {
    const apiClient = fakeClient();
    await fetchTrials({
      apiClient,
      patientInfo: { disease: "MM" },
      filters: { country: "US" },
    });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/trials/match/",
      { patient_info: { disease: "MM" } },
      { params: { country: "US" } },
    );
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  it("treats an empty patientInfo object as no inline payload and GETs instead", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient, patientInfo: {}, personId: 7 });
    expect(apiClient.post).not.toHaveBeenCalled();
    expect(apiClient.get).toHaveBeenCalledWith("/trials/", {
      params: { person_id: "7" },
    });
  });

  it("GETs /trials/?person_id= for the server-side resolver path", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient, personId: 42, filters: { sort: "matchScore" } });
    expect(apiClient.get).toHaveBeenCalledWith("/trials/", {
      params: { sort: "matchScore", person_id: "42" },
    });
  });

  it("GETs /trials/ with no person_id when neither patient context is given", async () => {
    const apiClient = fakeClient();
    await fetchTrials({ apiClient });
    expect(apiClient.get).toHaveBeenCalledWith("/trials/", { params: {} });
  });
});
