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
import type { AdvancedStatus, TrialStateAdapter } from "../federation/state";
import type {
  FilterState,
  PatientInfo,
  TrialDetailResponse,
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
  /** Make the next response — whichever request arrives first, list or
   *  detail — fail with this status. DRF's paginator answers a page past
   *  the end with a 404, which is the case most tests here arm it for. */
  failNextWith: (status: number) => void;
  /** Replace what the list returns from now on. */
  setResponse: (response: Partial<TrialsResponse>) => void;
  /** Leave `/form-settings/` unanswered, to see what renders while the option
   *  catalog is still in flight. */
  holdFormSettings: () => void;
  /** Override fields on every trial's detail response from now on.
   *  Whatever is NOT overridden follows the trial actually asked for — so
   *  the id and title track the row that was clicked unless a test pins
   *  them deliberately. */
  setDetail: (detail: Partial<TrialDetailResponse>) => void;
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

/** A detail response, as `GET /trials/{id}/` returns it.
 *
 *  The fake used to answer a detail request with the LIST payload, which has
 *  no `briefTitle` and no `details` — so the detail page rendered blank and
 *  any test about what it shows would have been asserting against an empty
 *  document. */
export function trialDetail(
  id: number,
  overrides: Partial<TrialDetailResponse> = {},
): TrialDetailResponse {
  return {
    trialId: id,
    studyId: `NCT${id}`,
    briefTitle: `Trial ${id}`,
    officialTitle: `Trial ${id}`,
    laySummary: "A summary.",
    matchScore: 90,
    goodnessScore: 80,
    matchingType: "eligible",
    details: { trialEligibilityAttributes: [] },
    groupNames: [],
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
  // Titles for the high-risk MCL panel. Deliberately NOT the codes with the
  // underscores swapped out: the panel must be shown taking them from the
  // catalog, not deriving them.
  highRiskMclCriteria: {
    options: [
      { value: "tp53_mutation", label: "TP53 mutation" },
      { value: "del17p", label: "del(17p)" },
      { value: "blastoid", label: "Blastoid morphology" },
      { value: "ki67_30", label: "Ki-67 >= 30%" },
    ],
  },
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
  let holdSettings = false;
  let detailOverrides: Partial<TrialDetailResponse> = {};

  const respond = (url: string, body?: unknown) => {
    if (url.includes("form-settings")) {
      return holdSettings
        ? new Promise(() => {
            /* never settles */
          })
        : Promise.resolve({ data: FORM_SETTINGS });
    }
    if (failWith != null) {
      const status = failWith;
      failWith = null;
      return Promise.reject(
        Object.assign(new Error(`Request failed with status code ${status}`), {
          response: { status },
        }),
      );
    }
    // A single trial, not the list: `/trials/7/` and `/trials/7/match/`.
    // Answered FOR THE ID ASKED FOR — one id-blind object would render
    // "Trial 1" whichever row was clicked, so a page that fetched or showed
    // the wrong trial would look right.
    const detailMatch = /^\/trials\/(\d+)\//.exec(url);
    if (detailMatch) {
      const id = Number(detailMatch[1]);
      return Promise.resolve({ data: { ...trialDetail(id), ...detailOverrides } });
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
    setDetail: (next: Partial<TrialDetailResponse>) => {
      detailOverrides = { ...detailOverrides, ...next };
    },
    holdFormSettings: () => {
      holdSettings = true;
    },
  };
}

export interface FakeState {
  adapter: TrialStateAdapter;
  /** The stored ids, readable after a write. They are the SAME arrays the
   *  reads return, so a test can assert that a write actually landed rather
   *  than only that a spy was called. */
  favorites: string[];
  registered: string[];
  /** Reads, counted. A write is supposed to invalidate the list it changed,
   *  and the only externally visible sign of that is a re-read. */
  reads: { favorites: number; registered: number };
}

/** A working adapter, backed by two arrays.
 *
 *  Typed as `TrialStateAdapter` rather than cast to `never` at the call
 *  site: the tests that went through `as never` would have kept compiling
 *  if a method were renamed, which is exactly the change that should break
 *  them. */
export function fakeState(
  initial: {
    favorites?: string[];
    registered?: string[];
    /** Trials a study team has moved past "registered". */
    advanced?: Record<string, AdvancedStatus>;
    overrides?: Partial<TrialStateAdapter>;
  } = {},
): FakeState {
  const favorites = [...(initial.favorites ?? [])];
  const registered = [...(initial.registered ?? [])];
  const reads = { favorites: 0, registered: 0 };

  const set = (list: string[], id: string, on: boolean) => {
    const at = list.indexOf(id);
    if (on && at === -1) list.push(id);
    if (!on && at !== -1) list.splice(at, 1);
  };

  const adapter: TrialStateAdapter = {
    listFavoriteIds: vi.fn(async () => {
      reads.favorites += 1;
      return [...favorites];
    }),
    listRegisteredIds: vi.fn(async () => {
      reads.registered += 1;
      return [...registered];
    }),
    setFavorite: vi.fn(async (id: string, on: boolean) => set(favorites, id, on)),
    setRegistered: vi.fn(async (id: string, on: boolean) => set(registered, id, on)),
    listAdvancedEnrollments: vi.fn(async () => ({ ...(initial.advanced ?? {}) })),
    getPreferences: vi.fn(async () => ({})),
    savePreferences: vi.fn(async () => undefined),
    resetPreferences: vi.fn(async () => undefined),
    ...initial.overrides,
  };

  return { adapter, favorites, registered, reads };
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
    state?: TrialStateAdapter;
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
        state={props.state}
      />
    </QueryClientProvider>,
  );
}
