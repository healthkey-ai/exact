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

import type {
  FilterState,
  PatientInfo,
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
}

/** Fetch the trial-match list. Two paths depending on inputs:
 *
 *  - **Inline patient profile** (`patientInfo` non-empty): POSTs to
 *    `/trials/match/` with `{ patient_info: … }` in the body. POST
 *    because both the Fetch spec and axios's XHR adapter forbid
 *    GET-with-body, and the patient payload is too large for a query
 *    string. The endpoint is a thin alias for the list action
 *    (`@action(methods=['post'], url_path='match')` on TrialsViewSet,
 *    PR #121) so the response shape is unchanged.
 *  - **Server-side resolver path** (`personId` only): GETs
 *    `/trials/?person_id=…`. EXACT's `resolve_patient_info` will
 *    fetch the patient from CTOMOP server-side. No body, no
 *    Fetch-spec issue.
 */
export async function fetchTrials({
  apiClient,
  patientInfo,
  personId,
  filters,
}: FetchTrialsArgs): Promise<TrialsResponse> {
  const params = filterStateToParams(filters);
  const hasInlinePayload = patientInfo != null && Object.keys(patientInfo).length > 0;

  if (hasInlinePayload) {
    const response = await apiClient.post<TrialsResponse>(
      "/trials/match/",
      { patient_info: patientInfo },
      { params },
    );
    return response.data;
  }

  if (personId != null) {
    params.person_id = String(personId);
  }
  const response = await apiClient.get<TrialsResponse>("/trials/", { params });
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
  if (filters.distance != null) out.distance = String(filters.distance);
  if (filters.distanceUnits) out.distanceUnits = filters.distanceUnits;
  if (filters.validatedOnly) out.validatedOnly = "true";
  if (filters.sponsor) out.sponsor = filters.sponsor;
  if (filters.register) out.register = filters.register;
  if (filters.searchTitle) out.searchTitle = filters.searchTitle;
  if (filters.type) out.type = filters.type;
  if (filters.sort) out.sort = filters.sort;
  return out;
}
