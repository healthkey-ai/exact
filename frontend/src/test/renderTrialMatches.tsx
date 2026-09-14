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
import type { MapRenderer } from "../federation/TrialsMap";
import type {
  AdvancedStatus,
  TrialStateAdapter,
  WriteOutcome,
} from "../federation/state";
import type { WritableFields } from "../federation/writable";
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
  /** Leave the NEXT list request unanswered, and hand back the release.
   *
   *  Anything about what the reader sees WHILE a request is in flight — the
   *  stale-data dimming, a loading line — is unobservable against a fake that
   *  resolves in the same tick: the in-flight state exists, but no assertion
   *  can be scheduled inside it. */
  deferNextList: () => () => void;
  /** Replace the graph payload's trials. Each entry is merged over the node
   *  the fake would have built for that trial. */
  setGraph: (trials: Array<Record<string, unknown>>) => void;
  /** Answer the next export with a file whose last line says it stopped
   *  early — what a stream that died halfway delivers when the server was
   *  still alive to say so. */
  truncateNextExport: () => void;
  /** Answer the next export with a file that just STOPS — no end marker and
   *  no apology, which is what a proxy cutting the response leaves behind. */
  cutNextExport: () => void;
  /** Cut the next export in the one place that looks finished: inside a quoted
   *  title holding a newline and the completion marker, so the file's last
   *  PHYSICAL line is the marker while its last RECORD is a half-written
   *  row. */
  cutNextExportInsideAQuotedMarker: () => void;
  /** Hold the next export open, and hand back the release. */
  holdNextExport: () => () => void;
  /** Hold the NEXT detail response open, and return the release. Lets a test
   *  see the page during a re-read rather than only after it: whether a save
   *  waits for the record it claims to have re-read is invisible once the
   *  refetch has already landed. */
  holdNextDetail: () => () => void;
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
  let deferList: ((value: unknown) => void) | null = null;
  // Separate from `deferList`, which is cleared the moment the request
  // arrives: a deferral is "armed" from the call until the release, and the
  // held request lives in the middle of that. Guarding on `deferList` alone
  // would let a second arm through while the first request was still held.
  let deferArmed = false;
  let truncateExport = false;
  let cutExport = false;
  let cutInsideQuote = false;
  let heldExport: { release: () => void } | null = null;
  let detailOverrides: Partial<TrialDetailResponse> = {};
  let graphOverrides: Array<Record<string, unknown>> | null = null;
  let heldDetail: { release: () => void } | null = null;

  const isListUrl = (url: string) =>
    url === "/trials/search/" || url === "/trials/search/match/";

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
    if (url.includes("/trials/export/")) {
      const body = cutInsideQuote
        ? 'Study ID,Title\nNCT1,"a title\n# end of export — not really'
        : cutExport
        ? // Cut mid-row, and the row before it holds a title with a NEWLINE
          // followed by the completion marker — `csv.writer` keeps a line
          // break inside a quoted field, so the marker starts a physical line
          // without starting a record.
          'Study ID,Title\nNCT1,"a title\n# end of export — not really"\nNCT2,Part'
        : truncateExport
          ? "Study ID\nNCT1\n# EXPORT INCOMPLETE — this file stopped early after 1 trials\n"
          : "Study ID\nNCT1\n# end of export — 1 trials\n";
      truncateExport = false;
      cutExport = false;
      cutInsideQuote = false;
      const answer = {
        data: new Blob([body], { type: "text/csv" }),
        headers: { "content-disposition": 'attachment; filename="trials-2026-09-11.csv"' },
      };
      if (heldExport) {
        const held = heldExport;
        heldExport = null;
        return new Promise((resolve) => {
          held.release = () => resolve(answer);
        });
      }
      return Promise.resolve(answer);
    }
    if (url.includes("/trials-graph/graph/")) {
      return Promise.resolve({
        data: {
          patient: { disease: "multiple myeloma" },
          trials: response.results.map((t) => ({
            nodeId: `trial:${t.trialId}`,
            trialId: t.trialId,
            studyId: t.studyId,
            briefTitle: t.briefTitle,
            matchScore: t.matchScore,
            goodnessScore: t.goodnessScore,
            match: {
              matched: [{ patientField: "disease", label: "Disease" }],
              missing: [{ patientField: "ecog", label: "ECOG" }],
              notMatched: [],
            },
            ...(graphOverrides?.find((o) => o.trialId === t.trialId) ?? {}),
          })),
        },
      });
    }
    // A single trial, not the list: `/trials/7/` and `/trials/7/match/`.
    // Answered FOR THE ID ASKED FOR — one id-blind object would render
    // "Trial 1" whichever row was clicked, so a page that fetched or showed
    // the wrong trial would look right.
    const detailMatch = /^\/trials\/(\d+)\//.exec(url);
    if (detailMatch) {
      const id = Number(detailMatch[1]);
      const answer = { data: { ...trialDetail(id), ...detailOverrides } };
      if (heldDetail) {
        const gate = heldDetail;
        heldDetail = null;
        return new Promise((resolve) => {
          gate.release = () => resolve(answer);
        });
      }
      return Promise.resolve(answer);
    }
    // Honour `trial_ids` the way the server does. Returning the same rows
    // for a narrowed request makes the fake agree with any implementation,
    // including one that ignores the filter — a test asserting "the wrong
    // rows are not shown" then cannot fail.
    const listData = () => {
      const ids = (body as { trial_ids?: string[] } | undefined)?.trial_ids;
      if (ids === undefined) return { data: response };
      const wanted = new Set(ids.map(String));
      const results = response.results.filter((t) => wanted.has(String(t.trialId)));
      return { data: { ...response, results, itemsTotalCount: results.length } };
    };
    // After the payload is decided, not instead of deciding it: a `trial_ids`
    // request is a list request, and deferring only the unnarrowed ones would
    // make `deferNextList` silently skip every state tab.
    // Gated on the URL, not on "whatever reached the end of this function":
    // an unrecognised path — `/normalize-ctomop-row/`, or the next endpoint
    // somebody adds — would otherwise consume a deferral armed for the list,
    // and the list request it was armed for would answer immediately while
    // the test waited for a state it had already passed through.
    if (deferList && isListUrl(url)) {
      const arm = deferList;
      deferList = null;
      // The payload is decided NOW, not on release. A `setResponse` made while
      // the request is held describes the NEXT response, not one the server
      // has already been asked for — resolving `listData()` late would let a
      // test retroactively change a response it is holding open, which is not
      // a thing a server can do.
      // Shallow-copied, so the held answer is this request's own object. The
      // rows inside are still the array the test supplied — nothing mutates a
      // response in place, and a fake that deep-copied would stop `trial()`
      // identities matching, which several tests compare on.
      const { data } = listData();
      const held = { data: { ...data } };
      return new Promise((resolve) => {
        arm(() => resolve(held));
      });
    }
    return Promise.resolve(listData());
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
    holdNextDetail: () => {
      const gate: { release: () => void } = { release: () => {} };
      heldDetail = gate;
      return () => gate.release();
    },
    setDetail: (next: Partial<TrialDetailResponse>) => {
      detailOverrides = { ...detailOverrides, ...next };
    },
    setGraph: (trials: Array<Record<string, unknown>>) => {
      graphOverrides = trials;
    },
    holdFormSettings: () => {
      holdSettings = true;
    },
    deferNextList: () => {
      // Two hops, because the release is handed out BEFORE the request it
      // releases has been made. `deferList` is called when the request
      // arrives and hands its resolver back through this closure.
      //
      // `released` is what makes the two orders equivalent. Without it,
      // releasing before the request arrived called the initial no-op and the
      // request then hung for ever — a test that armed the deferral, released
      // it, and only then triggered the request would time out rather than
      // fail, which is the least useful way for a test to be wrong.
      if (deferArmed) {
        // Loud, because the failure is silent: the second arm would replace
        // the first closure, the first `release()` would flip a flag nobody
        // reads, and that request would hang — a test that TIMES OUT rather
        // than fails, which is what this whole mechanism was fixed to stop
        // doing.
        throw new Error(
          'deferNextList is already armed: release the held request before ' +
            'arming another, or the first one hangs.',
        );
      }
      deferArmed = true;
      let released = false;
      let resolveArrived: (() => void) | null = null;
      deferList = (resolver) => {
        if (released) {
          (resolver as () => void)();
          return;
        }
        resolveArrived = resolver as () => void;
      };
      return () => {
        released = true;
        deferArmed = false;
        deferList = null;
        resolveArrived?.();
        resolveArrived = null;
      };
    },
    truncateNextExport: () => {
      truncateExport = true;
    },
    cutNextExport: () => {
      cutExport = true;
    },
    cutNextExportInsideAQuotedMarker: () => {
      cutInsideQuote = true;
    },
    holdNextExport: () => {
      const slot = { release: () => {} };
      heldExport = slot;
      return () => slot.release();
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
  reads: { favorites: number; registered: number; writable: number };
  /** What the patient record holds, after any writes. The same object the
   *  fake echoes back, so a test can assert the value landed rather than
   *  only that a spy was called. */
  record: Record<string, unknown>;
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
    /** The writable-fields descriptor. Absent means the host supplies no
     *  editing pair at all — which is the default, so every test that does
     *  not opt in keeps proving the page stays read-only. */
    writable?: WritableFields;
    record?: Record<string, unknown>;
    overrides?: Partial<TrialStateAdapter>;
  } = {},
): FakeState {
  const favorites = [...(initial.favorites ?? [])];
  const registered = [...(initial.registered ?? [])];
  const reads = { favorites: 0, registered: 0, writable: 0 };
  const record: Record<string, unknown> = { ...(initial.record ?? {}) };

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
    // Only when the test asked for it. The pair is all-or-nothing, so a fake
    // that always had it would never exercise the read-only path that every
    // host without a writer gets.
    ...(initial.writable
      ? {
          getWritableFields: vi.fn(async () => {
            reads.writable += 1;
            return initial.writable!;
          }),
          setPatientField: vi.fn(async (field: string, value: unknown) => {
            record[field] = value;
            return { status: "saved", value } as WriteOutcome;
          }),
        }
      : {}),
    ...initial.overrides,
  };

  return { adapter, favorites, registered, reads, record };
}

export interface RenderTrialMatchesResult extends RenderResult {
  /** Re-render with different props, keeping the same QueryClient — which is
   *  what a host re-rendering looks like from in here. A fresh `render` would
   *  start a new cache and prove nothing about what survives. */
  setProps: (next: TrialMatchesProps) => void;
  queryClient: QueryClient;
}

export interface TrialMatchesProps {
  patientInfo?: PatientInfo | null;
  personId?: string | number;
  // Typed, not `Record<string, unknown>` + `as never`: that turned off
  // checking for every test, so a typo'd filter name compiled and the
  // test asserted nothing.
  initialFilters?: FilterState;
  renderMap?: MapRenderer;
  state?: TrialStateAdapter;
}

export function renderTrialMatches(
  api: FakeApi,
  props: TrialMatchesProps = {},
): RenderTrialMatchesResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      // A test that retries hides the error path this suite is here to
      // exercise, and one that refetches on focus makes the request log
      // depend on which window jsdom thinks is focused.
      //
      // A test that wants a background refetch asks for one through the
      // returned `queryClient`. Not a preference: no window-focus trigger
      // could be made to fire here. Measured four ways against this suite —
      // `focusManager.setFocused(false)` then `(true)`; the same with
      // `Date.now` stubbed past `staleTime` before the render; the same with
      // `refetchOnWindowFocus: 'always'`, which bypasses staleness
      // altogether; and real `visibilitychange` + `focus` DOM events, which
      // are what React Query subscribes to. All four left the request count
      // at one.
      //
      // A review measured the opposite, with a `Date.now` stub, and put it
      // down to `staleTime: 30_000` in `hooks.ts`. That did not reproduce
      // here, and `'always'` rules staleness out on its own. The cause is
      // unresolved, which is the reason not to build on the trigger: a test
      // driven by something we cannot agree fires is a test that can pass by
      // never reaching its own assertion. `refetchQueries` is unambiguous and
      // reaches the same state.
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  });
  const tree = (next: TrialMatchesProps) => (
    <QueryClientProvider client={queryClient}>
      <TrialMatches
        apiClient={api.client}
        queryClient={queryClient}
        // `??` would swap in the default for an EXPLICIT null, so a test
        // about the no-patient path would quietly run with a patient.
        patientInfo={
          next.patientInfo === undefined
            ? { disease: "multiple myeloma" }
            : next.patientInfo
        }
        personId={next.personId}
        initialFilters={next.initialFilters}
        renderMap={next.renderMap}
        state={next.state}
      />
    </QueryClientProvider>
  );
  // Cumulative, not merged against the original every time: two consecutive
  // `setProps` calls would otherwise discard the first, so a test that changed
  // the patient and then the person id would silently be testing the original
  // patient again. Merged rather than replaced for the same class of reason —
  // a `setProps({ patientInfo })` over a render carrying a `state` adapter
  // must not unmount the adapter and turn a bookmark assertion into one about
  // the no-adapter path.
  let current: TrialMatchesProps = props;
  const result = render(tree(current));
  return {
    ...result,
    queryClient,
    setProps: (next: TrialMatchesProps) => {
      current = { ...current, ...next };
      result.rerender(tree(current));
    },
  };
}
