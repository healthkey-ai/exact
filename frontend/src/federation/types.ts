// TypeScript mirrors of the response shapes EXACT's `/trials/` endpoint
// returns. Source of truth: `trials/api/trials_serializers.py`
// (`TrialSerializer.to_representation` for the list and detail shape).
// JSON is camelCased server-side, so the keys match the serializer
// names verbatim. Types are kept permissive on purpose — the API
// echoes patient context verbatim and new fields land on the backend
// faster than the UI; failing closed on every new field would make
// the remote brittle.

import type { AxiosInstance } from "axios";
import type { QueryClient } from "@tanstack/react-query";

import type { MapRenderer } from "./TrialsMap";

import type { TrialPreferenceStore, TrialStateAdapter } from "./state";

/** Sparse, schema-tolerant patient payload — mirrors EXACT's stateless
 *  `PatientInfo` Python class. Keys are camelCase as sent over the wire.
 *  See `trials/services/patient_info/patient_info.py`. */
export type PatientInfo = Record<string, unknown>;

/** Per-trial verdict from the matcher. The serializer only ever emits
 *  `'eligible'` or `'potential'` (the backend filters not-eligibles
 *  out of the queryset before serialization), but the type union
 *  includes `'not_eligible'` so a host that paints a manual
 *  not-eligible group via a separate call doesn't fight the types. */
export type MatchingType = "eligible" | "potential" | "not_eligible";

export interface ClosestLocationGeoPoint {
  latitude: number;
  longitude: number;
}

/** Per-attr explanation shape (only populated for `potential` trials,
 *  driven by `attrs_to_fill_in`). */
export interface AttributeToFillIn {
  /** Trial-side attribute the patient profile is missing for this match.
   *  Permissive shape — server-side adds fields as the explainer evolves. */
  [key: string]: unknown;
}

/** A single trial in the list/search response. */
export interface TrialMatch {
  trialId: number;
  studyId: string;
  briefTitle: string;
  officialTitle: string;
  phase: string[];
  disease: string | null;
  recruitingStatus: string;
  sponsor: string;
  link: string;
  trialType: string | null;
  /** Locations are returned as a list of titles ordered by distance to
   *  the patient (closest first). The full Location records aren't
   *  included — call `/locations/?country_id=` for that. */
  location: string[];
  interventionTreatments: unknown;
  postedDate: string | null;
  lastUpdateDate: string | null;
  firstEnrolment: string | null;
  enrollmentCount: number | null;
  patientBurdenScore: number | null;
  goodnessScore: number | null;
  matchScore: number | null;
  /** `null` when the request carried no patient: `eligible` is a claim
   *  about a person, and with nobody named there is nothing to claim
   *  (#456). Narrow before comparing — `!== "eligible"` reads a
   *  patient-less row as potential. */
  matchingType: MatchingType | null;
  /** Stringified human-friendly stages — `"Stage I, Stage II"`. */
  stage: string;
  attributesToFillIn: AttributeToFillIn[];
  closestLocationGeoPoint: ClosestLocationGeoPoint | null;
  distance: number | null;
  distanceUnits: "km" | "miles" | null;
  /** Permissive index so the detail serializer's extra fields surface
   *  without a type widening on every backend change. */
  [key: string]: unknown;
}

/** Per-tab totals over the whole matched corpus, not over the rows in this
 *  response — so the Eligible badge does not read 0 while the user is on the
 *  Potential tab. Absent when the server could not judge: no patient context,
 *  or `?type=all`, whose admin branch skips the eligibility filter. Treat
 *  absence as "unknown", never as zero. (EXACT #417.) */
export interface TabCounts {
  eligible: number;
  potential: number;
}

export interface TrialsResponse {
  /** Total number of pages (not items). Use `itemsTotalCount` for total items. */
  count: number;
  itemsTotalCount: number;
  next: string | null;
  previous: string | null;
  results: TrialMatch[];
  tabCounts?: TabCounts;
}

/** Whether a value the reader supplies survives, and how not.
 *
 *  `always` — a write is overwritten on the next match. `sometimes` — it may
 *  be, under `condition`. `never-stored` — the value is computed on read, so
 *  the write is not even accepted. `null` or absent — EXACT leaves it alone,
 *  which is NOT the same as PROMOP accepting the write.
 *
 *  `condition` is prose written for a reader: show it, do not parse it. */
export type RecomputedWhen =
  | { when: "always" }
  | { when: "never-stored" }
  | { when: "sometimes"; condition: string }
  | null;


/** One value a composite row is computed from.
 *
 *  Sent under `subform_details` — snake_case, because EXACT does not camelise
 *  its responses and these keys are built by hand.
 *
 *  This is how a computed row is edited. The row itself carries no control:
 *  EXACT derives TNBC status from the receptor statuses, CRAB from calcium
 *  and creatinine and the rest, so writing the row would be undone by the
 *  next match. The entries beside it are those inputs, and they are raw data
 *  — with one exception worth knowing: the therapy groups, where
 *  `first_line_therapy` and its date and outcome are derived as well and
 *  therefore say so. */
export interface SubformEntry {
  /** camelCase, for display and for React keys. */
  name: string;
  label: string;
  type: string;
  value: unknown;
  options?: { value: unknown; label: string }[] | null;
  /** The patient attribute this entry IS, in the record's spelling. Sent
   *  rather than derived: un-camelising is not reliable here. */
  upatientField?: string | null;
  /** Whether EXACT recomputes it — see `TrialDetailField.upatientRecomputed`. */
  upatientRecomputed?: boolean;
  /** The same answer with its reason — see
   *  `TrialDetailField.upatientRecomputedWhen`.
   *
   *  Declared because the server sends it here too, and this interface has no
   *  index signature: without the field the key is simply unreachable from
   *  the component that would render it. The server side and this interface
   *  were written on either side of a merge, and neither half was wrong on
   *  its own. */
  upatientRecomputedWhen?: RecomputedWhen;
  /** The unit the trial's threshold is in. */
  units?: string;
  /** The unit the PATIENT's value is stored in — the one to show and to type
   *  in, because the composite above converts through it. */
  uunits?: string;
}

/** One row in a trial-detail `details` group. Mirrors EXACT's
 *  `TrialTemplates` field shape (built server-side, camelCased on the wire).
 *  `value` is the trial's required value; `uvalue` is the patient's value;
 *  `matchingType` is the per-attribute verdict. Permissive — the server adds
 *  fields as the templates evolve. Source: `trials/services/trial_details/`. */
export interface TrialDetailField {
  name: string;
  label: string;
  type: string;
  value: unknown;
  options?: { value: unknown; label: string }[] | null;
  matchingType?:
    | "matched"
    | "not_matched"
    // The trial asked for this attribute but the patient's data is missing.
    | "unknown"
    // The trial placed no constraint on it at all — nothing was checked, so
    // neither a tick nor a mismatch belongs on the row.
    | "not_evaluated"
    | string;
  ufield?: string | null;
  /** The canonical patient attribute this row is about, in the spelling the
   *  patient record uses (`hemoglobin_g_dl`), or null when the row is not
   *  about a patient attribute at all.
   *
   *  Not derivable from `ufield`: that is camelCase, and un-camelising is not
   *  mechanical here — `p53_ihc` camelises to `p53Ihc`, which a standard
   *  snake-caser turns back into `p_53_ihc`, a field nobody has. EXACT sends
   *  the canonical name rather than letting each client guess (#421).
   *
   *  It names the attribute. Whether it can be WRITTEN is a different
   *  question, answered by PROMOP's descriptor — see `writable.ts`. */
  upatientField?: string | null;
  /** Whether EXACT recomputes this attribute on every match.
   *
   *  If it does, a value written upstream does not survive: it is replaced
   *  from the inputs before the matcher sees it. PROMOP will still ACCEPT the
   *  write — the two answer different questions — so a client that asks only
   *  PROMOP offers a box whose effect is undone with no error anywhere
   *  (#449). */
  upatientRecomputed?: boolean;
  /** The same answer with its reason.
   *
   *  `upatientRecomputed` is true for three different situations and is no
   *  longer a safe sole input: `always` (a write is overwritten), `sometimes`
   *  (it may be, under `condition`) and `never-stored` (the value is computed
   *  on read, so the write is not even accepted).
   *
   *  A control gated on the boolean alone hides `mipiRisk` from every
   *  non-MCL patient, whose `mipiRisk` EXACT never touches. `condition` is
   *  prose written for a reader — show it, do not parse it. */
  upatientRecomputedWhen?: RecomputedWhen;
  /** The values this row is computed from, when it is computed from any.
   *  Snake_case on the wire. */
  subform_details?: SubformEntry[] | null;
  uvalue?: unknown;
  utype?: string;
  uoptions?: { value: unknown; label: string }[] | null;
  ureadonly?: boolean;
  /** Unit for the trial's required `value` (e.g. "mg/dL"). */
  units?: string;
  /** Unit for the patient's `uvalue` — may differ from `units`. */
  uunits?: string;
  [key: string]: unknown;
}

/** One high-risk MCL criterion, reported in ELIGIBILITY terms: on an
 *  EXCLUDED criterion `matched` means the patient is confirmed clear of it and
 *  `not_matched` means they have it and are ruled out — the inverse of an
 *  inclusion criterion. `unknown` means the source data is missing either way. */
export interface MclCriterion {
  code: string;
  status: "matched" | "not_matched" | "unknown";
}

/** `highRiskMclCriteriaBreakdown` from `GET /trials/{id}/` — per-criterion
 *  explainability for an attribute whose rule is really "at least N of these,
 *  none of those, or any one of these" (#4408). Absent, or null, for a trial
 *  that gates on no high-risk criteria and for a request with no patient.
 *
 *  Codes only: titles live in the `highRiskMclCriteria` options from
 *  `/form-settings/`, so the catalog stays the one place that names them. */
export interface HighRiskMclCriteriaBreakdown {
  aggregate: "matched" | "not_matched" | "unknown" | "not_evaluated" | string;
  /** How many of `required` are needed. At least 1. */
  minCount: number;
  matchedCount: number;
  required: MclCriterion[];
  excluded: MclCriterion[];
  sufficientAny: MclCriterion[];
}

/** One eligibility attribute inside a graph trial's `match` buckets. */
export interface GraphMatchItem {
  trialField?: string | null;
  patientField?: string | null;
  /** The same patient attribute in its canonical snake_case form (#421).
   *  Named by the server because deriving it here is not safe — `p53_ihc`
   *  camelizes to `p53Ihc`, which a standard snake-caser turns back into
   *  `p_53_ihc`, a field nobody has. */
  patientFieldCanonical?: string | null;
  /** Whether the value is edited through a subform. `null` where there is no
   *  patient field to edit at all, which is not the same as `false`. */
  patientFieldHasSubform?: boolean | null;
  label?: string | null;
  trialValue?: unknown;
  patientValue?: unknown;
  dependencies?: string[];
  dependencies_labels?: string[];
}

/** One trial node from `/trials-graph/graph/`. The three buckets describe the
 *  PATIENT against this trial's requirements: met, contradicted, and not
 *  known. An attribute the trial never constrained is in none of them. */
export interface GraphTrialNode {
  nodeId: string;
  trialId: number;
  studyId: string;
  studyUrl?: string | null;
  briefTitle?: string | null;
  recruitmentStatus?: string | null;
  sponsorName?: string | null;
  link?: string | null;
  goodnessScore?: number | null;
  matchScore?: number | null;
  match: {
    matched: GraphMatchItem[];
    notMatched: GraphMatchItem[];
    missing: GraphMatchItem[];
  };
}

export interface TrialsGraphResponse {
  patient: Record<string, unknown>;
  trials: GraphTrialNode[];
}

export interface GroupName {
  value: string;
  label: string;
}

/** Response of `GET /trials/{id}/` (and `POST /trials/{id}/match/`) —
 *  `TrialDetailsSerializer`. Keys are camelCased server-side. The grouped
 *  `details` (e.g. `general`, `trialEligibilityAttributes`) drive the
 *  Required / Your-Value table. */
export interface TrialDetailResponse {
  trialId: number;
  studyId: string;
  register?: string | null;
  briefTitle: string;
  officialTitle: string;
  locationsName?: string[] | null;
  interventionTreatments?: unknown;
  sponsorName?: string | null;
  link?: string | null;
  recruitmentStatus?: string | null;
  phases?: string[] | null;
  trialType?: string | null;
  briefSummary?: string | null;
  laySummary?: string | null;
  participationCriteria?: string | null;
  matchScore: number | null;
  goodnessScore: number | null;
  /** The verdict for THIS patient. Unlike the list — where the queryset drops
   *  the not-eligibles before serializing — the detail endpoint returns the
   *  trial by id without that filter and scores it with the conflict-aware
   *  Python matcher, so `not_eligible` really does arrive here
   *  (`TrialDetailSerializer.to_representation`). `null` without a patient. */
  matchingType?: MatchingType | null;
  details: Record<string, TrialDetailField[]>;
  groupNames: GroupName[];
  /** Detail view only, and only with a patient. Null for a trial that gates on
   *  no high-risk MCL criteria — which is every trial outside that disease. */
  highRiskMclCriteriaBreakdown?: HighRiskMclCriteriaBreakdown | null;
  [key: string]: unknown;
}

/** Filter prefs the UI surfaces. Keys mirror what
 *  `study_preferences_from_query_params` consumes (camelCase). */
export interface FilterState {
  recruitmentStatus?: string;
  country?: string;
  region?: string;
  trialType?: string;
  /** A list since CB #4663 made the control a multiselect; the server
   *  answers with the union. Sent as one comma-separated `trialPurpose`
   *  param — see `filterStateToParams`. */
  trialPurpose?: string[];
  studyType?: string;
  distance?: number;
  distanceUnits?: "km" | "miles";
  validatedOnly?: boolean;
  sponsor?: string;
  register?: string;
  /** Free-text title search — server matches `briefTitle` / `officialTitle`. */
  searchTitle?: string;
  /** Free-text intervention/treatment keyword search. */
  searchTreatment?: string;
  /** Trial phase — keeps trials at that phase **or later**. Trials whose
   *  phase was never ingested drop out of every value, including the
   *  lowest (EXACT #417). */
  phase?: string;
  /** Either an ISO date — `2026-01-01`, or the `T`/`Z` forms — meaning "on
   *  or after that day", or digits meaning how many YEARS back to accept.
   *
   *  The date spelling was silently dropped until #429: `by_date_since` read
   *  the value through `cast_str_to_int`, which takes digits only, so a date
   *  became `None` and nothing was filtered. CB's panel has always rendered a
   *  date picker and PATCHed an ISO value into that. Both work now.
   *
   *  A bare four-digit value is still read as a COUNT, so `2026` means
   *  "within 2026 years", not the year 2026 — send `2026-01-01` for that. The
   *  count is bounded client-side (`isUsableLastUpdate`): `"0"` reads as no
   *  limit at all, and a few thousand takes the backend's date arithmetic
   *  below year 1 and 500s every search.
   *
   *  Either way, trials whose `last_update_date` is null are kept. */
  lastUpdate?: string;
  /** "type" param. `eligible` / `potential` narrow server-side; `all`
   *  switches to the admin corpus, which skips the eligibility filter and
   *  several study preferences with it (EXACT #424), and suppresses
   *  `tabCounts`. */
  type?: "eligible" | "potential" | "all";
  /** Sort key. Defaults to `goodnessScore`. */
  sort?:
    | "goodnessScore"
    | "matchScore"
    | "distance"
    | "status"
    | "phase"
    | "updated"
    | "enrollment"
    | "patientBurdenScore";
  /** How the Suitability Score weighs its four terms, 0 and up.
   *
   *  Not filters: they change the ORDER and the percentage on each card, not
   *  which trials come back, so they are deliberately outside `PANEL_FIELDS`
   *  — the Filters badge does not count them and Reset does not clear them.
   *  Each defaults to 25 server-side, and only a weight that differs from
   *  that goes on the wire. All four at zero is not an error: the server
   *  reads a zero sum as "no opinion" and scores 25/25/25/25. */
  benefitWeight?: number;
  patientBurdenWeight?: number;
  riskWeight?: number;
  distancePenaltyWeight?: number;
}

/** Public props for the federated `./TrialMatches` export. Host-agnostic:
 *  the host wires its own axios instance (with `Authorization: Token …`)
 *  and either a person_id (CTOMOP federation path) or an inline payload
 *  (legacy CB path). */
export interface TrialMatchesProps {
  /** Axios instance pre-configured with `baseURL` (e.g. `/api`) and
   *  `Authorization: Token <…>` header. Required. */
  apiClient: AxiosInstance;
  /** Optional shared TanStack QueryClient — when omitted, the component
   *  spins up its own. Set when the host wants to share the cache. */
  queryClient?: QueryClient;
  /** CTOMOP person_id. Mutually exclusive with `patientInfo` — when
   *  both are provided, `patientInfo` wins (matches the server-side
   *  precedence in `resolve_patient_info`). */
  personId?: string | number;
  /** Inline patient payload. The other half of the resolver contract. */
  patientInfo?: PatientInfo | null;
  /** Initial filter state. The UI lets the user mutate from here. */
  initialFilters?: FilterState;
  /** Called when the user opens a trial card / detail view. */
  onTrialSelect?: (trial: TrialMatch) => void;
  /** The trial the HOST has in its address bar.
   *
   *  Passing this — `null` included — hands the remote's navigation to the
   *  host: it renders the trial named here, and asks for a change through
   *  `onTrialIdChange` instead of moving history itself. Omit it, and the
   *  remote keeps its own selection and pushes a synthetic history entry so
   *  the browser's back button returns to the list; that is what a host
   *  with no route for a trial gets, and it is unchanged.
   *
   *  Pass `onTrialIdChange` with it. Alone, this prop would leave the remote
   *  with nowhere to report a change and the reader stuck on a detail page
   *  whose back button does nothing, so half a contract is read as none: the
   *  remote keeps its own selection and says so at the console.
   *
   *  Exactly one of the two pushes. Both pushing is how a back button starts
   *  needing two presses.
   *
   *  A string is taken as it comes — a URL segment is a string — and handed
   *  to the detail request. An id nothing answers for lands on the detail
   *  page's own "we could not find that trial", which is a page with a way
   *  back rather than an empty frame. */
  trialId?: number | string | null;
  /** The reader opened a trial, or left one — `null` for the list.
   *
   *  Only meaningful alongside `trialId`: it is how the remote asks the host
   *  to change the URL it is being told to render. `onTrialSelect` still
   *  fires for an opening, and carries the whole row; this one carries what
   *  goes in an address bar. */
  onTrialIdChange?: (trialId: number | null) => void;
  /** The record took an inline edit, and here is what it says those fields
   *  are now.
   *
   *  Called once a batch of edits has settled AND the record has been
   *  re-read, with the values the RECORD answered — not always what was
   *  typed, since it canonicalises units, dates and cleared values on the
   *  way back. A field the record did not mention is left out.
   *
   *  A host that supplies `patientInfo` MUST refresh it here, and is the
   *  only one that can: its payload wins server-side over `personId`, so
   *  until the payload carries the new value every query this remote makes
   *  is answered from the old one and the page keeps showing what the
   *  reader just changed away from (#555).
   *
   *  **The payload must name the patient** — `personId`, `person_id`,
   *  `externalId`, `external_id`, `patientId`, `patient_id` or `id`, any one
   *  of them, non-blank. This remote has no other
   *  way to tell a refresh of the same profile from a switch to a different
   *  one, so with an id-less payload the refresh you make here reads as a
   *  change of patient: the trial page the edit was made from closes, and an
   *  edit made while your refresh is in flight is written but never reported
   *  back. A console warning says so when the prop is wired to such a
   *  payload.
   *
   *  **Re-read the profile; do not merge these fields into the payload.**
   *  They are named the way the patient RECORD names them
   *  (`hemoglobin_g_dl`), which is not the vocabulary a payload is in
   *  (`hemoglobin_level`, and in different units for some) — the two are
   *  bridged by `/normalize-ctomop-row/`, on the way in. Merged raw, a field
   *  is dropped by the server's own filter or, worse, sits beside the
   *  normalised copy it was supposed to replace. They are reported so a host
   *  can log them, show them, or map them deliberately.
   *
   *  Ordering: the queue runs one batch at a time and each report waits on
   *  that batch's own re-read — including when a later batch supersedes it,
   *  which chains onto the newer read rather than cutting the wait short.
   *  The one case that skips it is the reader leaving the detail page
   *  before the write settles: nothing is observing that query, so nothing
   *  refetches and the report arrives having re-read nothing. It still
   *  carries what the record answered. What this cannot order is the HOST's
   *  own work: if your refresh is asynchronous, make sure an older
   *  one cannot land after a newer one and put the payload back. React
   *  Query's `invalidateQueries` on one key does that for you — the newest
   *  fetch wins; a hand-rolled fetch does not.
   *
   *  Deliberately not solved by chaining the calls here: waiting on the
   *  host's promise lets a host that never settles block every later report,
   *  and the page would go quiet on writes that succeeded.
   *
   *  A host that throws, or returns a promise that rejects, does not turn a
   *  successful write into a failed one. Nothing is reported after this
   *  remote unmounts or after the host takes the state adapter away: the
   *  payload names no patient, so a host could not tell that what it was
   *  handed belongs to whoever was on screen a moment ago. */
  onPatientRecordChanged?: (fields: Record<string, unknown>) => void;
  /** Draws the map behind the list/map view mode.
   *
   *  A function rather than an API key, because rendering tiles means loading
   *  a third-party script into the HOST's page — billed to its key, subject to
   *  its CSP, watching its document. That is the host's decision to take
   *  explicitly. Without it the map view still works and the places are
   *  listed; see `TrialsMap`.
   */
  renderMap?: MapRenderer;
  /** Where the patient's bookmarks, registrations and saved filters live.
   *
   *  Optional, and its absence is not a degraded mode so much as a smaller
   *  one: without it the Favorites and Registered tabs and the bookmark
   *  control are not rendered at all, because they would be controls with
   *  nowhere to write. Everything else works unchanged.
   *
   *  `createPromopState` builds the default implementation from an
   *  authenticated PROMOP client; a host that reaches the same data another
   *  way implements the interface instead. */
  state?: TrialStateAdapter;
  /** Where the patient's saved search settings live, for a host that can
   *  answer that and nothing else.
   *
   *  `state` already carries these, and wins when both are given. This is
   *  for the other kind of host: the standalone widget build, mounted by an
   *  app that keeps these settings on its own user row and has no store for
   *  bookmarks or registrations. Passing a stub `state` to reach them would
   *  light up the Favorites and Registered tabs and the bookmark control,
   *  which would then have nowhere to write.
   *
   *  A host with a full adapter passes it as `state`, not here: passed here
   *  it type-checks — every adapter IS a preference store — and the tabs and
   *  the bookmark it could have answered for simply do not render.
   *
   *  Without either, the settings go to this browser's `localStorage`
   *  (`preferences.ts`), which survives a reload. Note what that means for a
   *  host that supplies a store persisting only PART of the set: the rest
   *  stops surviving a reload, because a store present at all replaces the
   *  browser-local one rather than joining it. */
  preferences?: TrialPreferenceStore;
}

/** Public props for the federated `./TrialMatchesBridge` export — the
 *  framework-agnostic mount used by hosts that are not React.
 *
 *  Lives here rather than in `TrialMatchesBridge.tsx` so a TypeScript host can
 *  type its `provider().render({ … })` call against the contract: `./types` is
 *  exposed, the bridge module is loaded at runtime.
 *
 *  `apiClient` and `queryClient` are omitted deliberately — the host passes
 *  data, not live objects, so it needs neither axios nor react-query. */
export interface TrialMatchesBridgeProps
  extends Omit<TrialMatchesProps, "apiClient" | "queryClient" | "personId"> {
  /** As `TrialMatchesProps["personId"]`, but `null` is accepted and means the
   *  same as omitting it: the hosts this bridge exists for have no types, and
   *  `personId: pid ?? null` is how they spell "I do not have one". Normalised
   *  before it reaches TrialMatches. */
  personId?: string | number | null;
  /** Service origin, e.g. `"https://exact-staging-….run.app"`. */
  baseUrl: string;
  /**
   * PRomop origin. When given (and neither `patientInfo` nor `personId` is
   * passed), the bridge loads the signed-in patient itself: PRomop's
   * `/patient-info/me/` with the caller's own token, then EXACT's
   * `/normalize-ctomop-row/`.
   *
   * That two-step is EXACT's business, not the host's, so it lives in the
   * bridge rather than being reimplemented by every host. It also has to be
   * the caller's token: EXACT's server-side `person_id` resolver is disabled
   * by default because its CTOMOP service token is not bound to the caller and
   * would let any `person_id` through (IDOR). Fetching "me" makes PRomop
   * enforce access.
   *
   * Note that passing `patientInfo: null` explicitly counts as the host having
   * answered ("this patient has no profile") and suppresses the fetch; omit
   * the prop entirely to have the bridge resolve.
   */
  ctomopBaseUrl?: string;
  /** Joined onto `baseUrl` to form the axios baseURL. Leading/trailing
   *  slashes are normalised, so `"api"` and `"/api"` behave the same.
   *
   *  Defaults to `""`, i.e. EXACT's API is assumed to live at the origin
   *  itself. Most deployments mount it under `/api` — pass it, or every call
   *  404s behind the generic error card. */
  apiBasePath?: string;
  /** Joined onto `ctomopBaseUrl` the same way. Defaults to `"/api"`; set it
   *  when PRomop is mounted elsewhere (e.g. `"/api/v1"`), otherwise the
   *  `/patient-info/me/` call 404s behind the generic error card. */
  ctomopApiBasePath?: string;
  /**
   * Resolves the caller's bearer token; the host owns authentication.
   *
   * The same token is sent to both `baseUrl` and `ctomopBaseUrl`, so it must
   * be valid at both services — an EXACT-only, audience-scoped token is not
   * enough when `ctomopBaseUrl` is set.
   *
   * Its *identity* is the session signal when no `sessionKey` is passed, in
   * which case changing it re-runs the self-driven patient resolution. Pass a stable function (module-level,
   * or memoised) so a re-render does not cost a redundant round trip — and do
   * hand over a new one when the signed-in user changes, or a mount reused
   * across a logout/login will keep showing the previous user's matches.
   */
  getToken?: () => Promise<string | null | undefined> | string | null | undefined;
  /**
   * Identifies the signed-in user. Change it and the bridge drops whatever it
   * resolved and resolves again; keep it and the bridge holds what it has.
   *
   * A mount reused across a logout/login would otherwise keep showing the
   * previous user's matches, so without a `sessionKey` the bridge falls back
   * to `getToken`'s identity as the signal. That is safe but blunt: a host
   * that builds `getToken` inline rebuilds it on every render, and each one
   * sends the user back to the loading state, losing the filters and the
   * query cache. Pass a `sessionKey` (a user id, a session id — any scalar
   * that changes with the user) and `getToken` can be as unstable as you like.
   *
   * `null`, `""`, `NaN` and a boolean all count as not passing one, and fall back to the
   * `getToken` signal rather than pinning every user to the same key: they are
   * what `auth.userId ?? null`, `user?.id ?? ""` and `Number(sub)` produce
   * when there is nothing to read, not user ids. Pass a scalar, and one that
   * is stable within a session — an object is a new value on every render, so
   * every render would reset the mount and re-resolve the patient.
   */
  sessionKey?: string | number | null;
}
