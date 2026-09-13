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

export interface TrialsResponse {
  /** Total number of pages (not items). Use `itemsTotalCount` for total items. */
  count: number;
  itemsTotalCount: number;
  next: string | null;
  previous: string | null;
  results: TrialMatch[];
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
  matchingType?: "matched" | "not_matched" | "unknown" | string;
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
  details: Record<string, TrialDetailField[]>;
  groupNames: GroupName[];
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
  /** "type" param — narrows to `eligible` / `potential` server-side. */
  type?: "eligible" | "potential";
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
