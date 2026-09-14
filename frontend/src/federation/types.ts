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
