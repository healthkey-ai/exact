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

import type { TrialStateAdapter } from "./state";

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
  matchingType: MatchingType;
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
  trialPurpose?: string;
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
  /** How many YEARS back to accept, as digits — not a date. `by_date_since`
   *  runs it through `cast_str_to_int`, which takes digits only, so an ISO
   *  date is silently dropped and nothing is filtered (#429). It also keeps
   *  trials whose `last_update_date` is null. */
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
  /** Draws the map behind the List/Map toggle.
   *
   *  A function rather than an API key, because rendering tiles means loading
   *  a third-party script into the HOST's page — billed to its key, subject to
   *  its CSP, watching its document. That is the host's decision to take
   *  explicitly. Without it the toggle still works and the places are listed;
   *  see `TrialsMap`.
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
}
