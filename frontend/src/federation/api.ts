// Axios wrappers for EXACT's `/trials/`, `/countries/`, and
// `/form-settings/` endpoints. The host hands us a pre-authenticated
// `AxiosInstance` (token + baseURL); these functions just shape the
// request and parse the response.
//
// **POST for inline patient context**: EXACT's `/trials/` historically
// accepted `patient_info` in a GET body (legacy CB contract), but the
// browser's Fetch API and axios v1's XHR adapter both prohibit
// GET-with-body. When the caller has an inline patient payload we
// instead POST to `/trials/match/` — a thin alias for the list endpoint
// added on the EXACT side (PR #121 / `TrialsViewSet.match`). When the
// caller has only a `personId`, we keep the GET path so the server-side
// CTOMOP resolver (#102) handles patient fetching.

import type { AxiosInstance } from "axios";

import { isUsableLastUpdate, isActiveDistance } from "./filters";
import type {
  FilterState,
  PatientInfo,
  TrialDetailResponse,
  TrialsResponse,
} from "./types";

interface FetchTrialsArgs {
  apiClient: AxiosInstance;
  /** Inline payload — wins over `personId` server-side, matches
   *  `resolve_patient_info` precedence. */
  patientInfo?: PatientInfo | null;
  /** CTOMOP person_id — server-side path via `?person_id=`. */
  personId?: string | number;
  /** Filter prefs, sent as query params. */
  filters?: FilterState;
  /** 1-indexed page number; omit or pass 1 for the first page. */
  page?: number;
  /** Rows per page. CB shows 10; the server default is 20. */
  limit?: number;
  /** Narrow to these trial ids — how the Favorites and Registered tabs
   *  work, since the ids live outside EXACT (#419).
   *
   *  `undefined` is "no such filter"; an EMPTY ARRAY is not. `[]` means the
   *  patient asked for their bookmarks and has none, and the server reads
   *  it the same way — collapsing the two would answer an empty Favorites
   *  tab with the whole corpus. */
  trialIds?: string[];
}

/** Fetch the trial-match list. Two paths depending on inputs:
 *
 *  - **Inline patient profile** (`patientInfo` non-empty): POSTs to
 *    `/trials/search/match/` with `{ patient_info: … }` in the body. POST
 *    because both the Fetch spec and axios's XHR adapter forbid
 *    GET-with-body, and the patient payload is too large for a query
 *    string. The alias binds the `search` action (EXACT #417), so the
 *    response is the sorted one and carries `tabCounts`.
 *  - **Server-side resolver path** (`personId` only): GETs
 *    `/trials/search/?person_id=…`. EXACT's `resolve_patient_info` will
 *    fetch the patient from PROMOP server-side. No body, no
 *    Fetch-spec issue.
 *
 *  Both go to `search` rather than `list`: only `search` reads `?sort=`
 *  and returns the per-tab counts. `list` orders by
 *  `-match_score, -posted_date, id` and ignores sorting entirely.
 */
/** Whether an inline payload is the effective patient context.
 *
 *  Both `patientInfo` and `personId` can be supplied, and the inline payload
 *  wins — server-side too, in `resolve_patient_info`. Exported because
 *  `TrialMatches` has to agree about which patient it is looking at: when the
 *  two disagreed, a host updating the inline payload while keeping a person
 *  id kept the previous patient's filters.
 *
 *  Empty is not supplied: `{}` carries no patient and the server would fall
 *  through to the `person_id` path, so this must too. */
export function hasInlinePatient(patientInfo: PatientInfo | null | undefined): boolean {
  return patientInfo != null && Object.keys(patientInfo).length > 0;
}

export async function fetchTrials({
  apiClient,
  patientInfo,
  personId,
  filters,
  page,
  limit,
  trialIds,
}: FetchTrialsArgs): Promise<TrialsResponse> {
  const params = filterStateToParams(filters);
  if (page != null && page > 1) params.page = String(page);
  if (limit != null) params.limit = String(limit);
  // `!== undefined`, never a truthiness test: `[]` is a filter that matches
  // nothing, not the absence of one.
  const body: Record<string, unknown> =
    trialIds !== undefined ? { trial_ids: trialIds } : {};

  if (hasInlinePatient(patientInfo)) {
    const response = await apiClient.post<TrialsResponse>(
      "/trials/search/match/",
      { patient_info: patientInfo, ...body },
      { params },
    );
    return response.data;
  }

  if (trialIds !== undefined) {
    // The id list only travels in a body, and the `person_id` path is a GET.
    // Posting the alias with no patient payload still reaches `search`, and
    // the server resolves the patient from the query param.
    if (personId != null) params.person_id = String(personId);
    const response = await apiClient.post<TrialsResponse>(
      "/trials/search/match/",
      body,
      { params },
    );
    return response.data;
  }

  if (personId != null) {
    params.person_id = String(personId);
  }
  const response = await apiClient.get<TrialsResponse>("/trials/search/", { params });
  return response.data;
}

interface FetchTrialDetailArgs {
  apiClient: AxiosInstance;
  trialId: number | string;
  patientInfo?: PatientInfo | null;
  personId?: string | number;
  /** Same study preferences the list uses (recruitmentStatus, distanceUnits,
   *  scoring weights, …). Sent so the detail's scores/distance/units agree
   *  with the card the user selected. */
  filters?: FilterState;
}

/** Fetch a single trial's detail (header meta, summary, and the per-patient
 *  eligibility table in `details.trialEligibilityAttributes`). Mirrors
 *  `fetchTrials`'s two patient-context paths:
 *
 *  - **Inline patient profile**: POSTs to `/trials/{id}/match/` with
 *    `{ patient_info: … }` — the detail-level alias for `retrieve` (the
 *    GET retrieve can't carry a body; see `TrialsViewSet.match_detail`).
 *  - **Server-side resolver path** (`personId` only, or no context): GETs
 *    `/trials/{id}/?person_id=…`.
 */
export async function fetchTrialDetail({
  apiClient,
  trialId,
  patientInfo,
  personId,
  filters,
}: FetchTrialDetailArgs): Promise<TrialDetailResponse> {
  const params = filterStateToParams(filters);

  if (hasInlinePatient(patientInfo)) {
    const response = await apiClient.post<TrialDetailResponse>(
      `/trials/${trialId}/match/`,
      { patient_info: patientInfo },
      { params },
    );
    return response.data;
  }

  if (personId != null) params.person_id = String(personId);
  const response = await apiClient.get<TrialDetailResponse>(`/trials/${trialId}/`, {
    params,
  });
  return response.data;
}

/** GET `/form-settings/?disease=` — option dicts for recruitment status,
 *  trial type, etc. Returns the full `all_options()` dict with
 *  per-disease overrides applied. */
export async function fetchFormSettings(
  apiClient: AxiosInstance,
  diseaseCode?: string,
): Promise<Record<string, { options: { value: string; label: string }[] }>> {
  const params = diseaseCode ? { disease: diseaseCode } : undefined;
  const response = await apiClient.get("/form-settings/", { params });
  return response.data;
}

/** POST `/normalize-ctomop-row/` — pipes a raw CTOMOP `patient_info`
 *  row through EXACT's `normalize_ctomop_row` (receptor statuses → codes,
 *  TNM stripping, therapy-outcome label → ID, refractory status,
 *  lab-value fallbacks, etc.) and returns the normalized row.
 *
 *  Used by the dev harness so that browser-side CTOMOP fetches (which
 *  skip the server-side resolver's normalization step) hand the
 *  matcher EXACT-shaped values instead of raw CTOMOP labels.
 *  Without this chain step, receptor / therapy / refractory fields
 *  silently read as "unknown". */
export async function normalizeCtomopRow(
  apiClient: AxiosInstance,
  row: PatientInfo,
): Promise<PatientInfo> {
  const response = await apiClient.post<PatientInfo>(
    "/normalize-ctomop-row/",
    row,
  );
  return response.data;
}

/** Convert the camelCase filter state to the query-string shape EXACT's
 *  view expects. The mapping is 1:1 with `study_preferences_from_query_params`
 *  in `trials/services/study_preferences.py`. Exported so the unit tests can
 *  lock the mapping against backend param drift. */
export function filterStateToParams(filters?: FilterState): Record<string, string> {
  const out: Record<string, string> = {};
  if (!filters) return out;
  if (filters.recruitmentStatus) out.recruitmentStatus = filters.recruitmentStatus;
  if (filters.country) out.country = filters.country;
  if (filters.region) out.region = filters.region;
  if (filters.trialType) out.trialType = filters.trialType;
  if (filters.trialPurpose) out.trialPurpose = filters.trialPurpose;
  if (filters.studyType) out.studyType = filters.studyType;
  // Only a radius the server will honour goes on the wire: zero is ignored
  // by `if study_info.distance:`, and a negative one passes that check and
  // becomes a negative geospatial radius.
  if (isActiveDistance(filters.distance)) out.distance = String(filters.distance);
  if (filters.distanceUnits) out.distanceUnits = filters.distanceUnits;
  if (filters.validatedOnly) out.validatedOnly = "true";
  if (filters.sponsor) out.sponsor = filters.sponsor;
  if (filters.register) out.register = filters.register;
  if (filters.searchTitle) out.searchTitle = filters.searchTitle;
  if (filters.phase) out.phase = filters.phase;
  // Checked at the wire, like `distance` above it, because the storage
  // check cannot see a value that never went through storage: a host's
  // `initialFilters` reaches here directly. `"3000"` is digits, passes the
  // server's own `cast_str_to_int`, and then takes
  // `datetime.now() - timedelta(...)` below year 1 — an OverflowError, and
  // a 500 on every search.
  if (isUsableLastUpdate(filters.lastUpdate)) {
    out.lastUpdate = filters.lastUpdate as string;
  }
  if (filters.searchTreatment) out.searchTreatment = filters.searchTreatment;
  if (filters.type) out.type = filters.type;
  if (filters.sort) out.sort = filters.sort;
  return out;
}
