// Harness for the component suite: a fake axios instance that records what
// was asked for, and a render helper that gives each test its own
// QueryClient.
//
// The fake is deliberately hand-written rather than a mocking library. The
// bugs this suite exists to catch are about WHICH request goes out and WHEN
// (#426), so the request log is the thing under test and it should be plain
// to read.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { AxiosInstance } from "axios";
import { vi } from "vitest";

import { TrialMatches } from "../federation/TrialMatches";
import type {
  FilterState,
  PatientInfo,
  TrialMatch,
  TrialsResponse,
} from "../federation/types";

export interface RecordedRequest {
  method: "get" | "post";
  url: string;
  params: Record<string, string>;
  body?: unknown;
}

export interface FakeApi {
  client: AxiosInstance;
  requests: RecordedRequest[];
  /** Requests to the trial LIST, in order. Deliberately not "anything under
   *  /trials/": `/trials/1/match/` is a detail request and counting it would
   *  quietly inflate every assertion about how many searches went out. */
  listRequests: () => RecordedRequest[];
  /** Requests for one trial's detail. */
  detailRequests: () => RecordedRequest[];
  /** Make the next list response a 404, as DRF's paginator does for a page
   *  past the end. */
  failNextWith: (status: number) => void;
  /** Replace what the list returns from now on. */
  setResponse: (response: Partial<TrialsResponse>) => void;
}

export function trial(id: number, overrides: Partial<TrialMatch> = {}): TrialMatch {
  return {
    trialId: id,
    studyId: `NCT${id}`,
    briefTitle: `Trial ${id}`,
    officialTitle: `Trial ${id}`,
    phase: ["PHASE2"],
    disease: "Multiple Myeloma",
    recruitingStatus: "RECRUITING",
    sponsor: "Sponsor",
    link: "",
    trialType: null,
    location: [],
    interventionTreatments: [],
    postedDate: null,
    lastUpdateDate: null,
    firstEnrolment: null,
    enrollmentCount: null,
    patientBurdenScore: null,
    goodnessScore: 80,
    matchScore: 90,
    matchingType: "eligible",
    stage: "",
    attributesToFillIn: [],
    closestLocationGeoPoint: null,
    distance: null,
    distanceUnits: null,
    ...overrides,
  };
}

const FORM_SETTINGS = {
  trialPurpose: { options: [{ value: "", label: "ALL" }, { value: "treatment", label: "Treatment" }] },
  trialType: {
    options: [
      { value: "", label: "ALL" },
      { value: "drug", label: "Drug" },
      { value: "device", label: "Device" },
    ],
  },
  recruitmentStatuses: { options: [{ value: "", label: "ALL" }, { value: "RECRUITING", label: "Recruiting" }] },
  phases: { options: [{ value: "", label: "ALL" }, { value: "PHASE3", label: "III" }] },
  register: { options: [{ value: "", label: "ALL" }] },
  allCountries: { options: [{ value: "", label: "Unknown" }] },
};

export function fakeApi(initial: Partial<TrialsResponse> = {}): FakeApi {
  const requests: RecordedRequest[] = [];
  let response: TrialsResponse = {
    count: 1,
    itemsTotalCount: 1,
    next: null,
    previous: null,
    results: [trial(1)],
    tabCounts: { eligible: 1, potential: 0 },
    ...initial,
  };
  let failWith: number | null = null;

  const respond = (url: string, body?: unknown) => {
    if (url.includes("form-settings")) return Promise.resolve({ data: FORM_SETTINGS });
    if (failWith != null) {
      const status = failWith;
      failWith = null;
      return Promise.reject(
        Object.assign(new Error(`Request failed with status code ${status}`), {
          response: { status },
        }),
      );
    }
    // Honour `trial_ids` the way the server does. Returning the same rows
    // for a narrowed request makes the fake agree with any implementation,
    // including one that ignores the filter — a test asserting "the wrong
    // rows are not shown" then cannot fail.
    const ids = (body as { trial_ids?: string[] } | undefined)?.trial_ids;
    if (ids !== undefined) {
      const wanted = new Set(ids.map(String));
      const results = response.results.filter((t) => wanted.has(String(t.trialId)));
      return Promise.resolve({
        data: { ...response, results, itemsTotalCount: results.length },
      });
    }
    return Promise.resolve({ data: response });
  };

  const record = (method: "get" | "post", url: string, a?: unknown, b?: unknown) => {
    const config = (method === "post" ? b : a) as { params?: Record<string, string> } | undefined;
    requests.push({
      method,
      url,
      params: config?.params ?? {},
      body: method === "post" ? a : undefined,
    });
    return respond(url, method === "post" ? a : undefined);
  };

  const client = {
    get: vi.fn((url: string, config?: unknown) => record("get", url, config)),
    post: vi.fn((url: string, body?: unknown, config?: unknown) =>
      record("post", url, body, config),
    ),
  } as unknown as AxiosInstance;

  return {
    client,
    requests,
    listRequests: () =>
      requests.filter(
        (r) => r.url === "/trials/search/" || r.url === "/trials/search/match/",
      ),
    detailRequests: () => requests.filter((r) => /^\/trials\/\d+\//.test(r.url)),
    failNextWith: (status: number) => {
      failWith = status;
    },
    setResponse: (next: Partial<TrialsResponse>) => {
      response = { ...response, ...next };
    },
  };
}

export function renderTrialMatches(
  api: FakeApi,
  props: {
    patientInfo?: PatientInfo | null;
    personId?: string | number;
    // Typed, not `Record<string, unknown>` + `as never`: that turned off
    // checking for every test, so a typo'd filter name compiled and the
    // test asserted nothing.
    initialFilters?: FilterState;
  } = {},
): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      // A test that retries hides the error path this suite is here to
      // exercise, and one that refetches on focus makes the request log
      // depend on which window jsdom thinks is focused.
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TrialMatches
        apiClient={api.client}
        queryClient={queryClient}
        // `??` would swap in the default for an EXPLICIT null, so a test
        // about the no-patient path would quietly run with a patient.
        patientInfo={
          props.patientInfo === undefined
            ? { disease: "multiple myeloma" }
            : props.patientInfo
        }
        personId={props.personId}
        initialFilters={props.initialFilters}
      />
    </QueryClientProvider>,
  );
}
