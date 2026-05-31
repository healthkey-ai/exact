// Axios wrappers for EXACT's `/trials/`, `/countries/`, and
// `/form-settings/` endpoints. The host hands us a pre-authenticated
// `AxiosInstance` (token + baseURL); these functions just shape the
// request and parse the response.
//
// **GET-with-body**: EXACT's `/trials/` reads `patientInfo` from the
// request body even on GET. Axios supports `data:` on GET requests, but
// some proxies / CDNs strip the body. The harness in #104 validates
// this works end-to-end through the Vite dev proxy; if it ever doesn't,
// the workaround is to switch to POST (the same view supports it via
// the underlying DRF mixin).

import type { AxiosInstance } from "axios";

import type {
  CountriesResponse,
  FilterState,
  PatientInfo,
  TrialMatch,
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

/** GET `/trials/` — list endpoint with matching. */
export async function fetchTrials({
  apiClient,
  patientInfo,
  personId,
  filters,
}: FetchTrialsArgs): Promise<TrialsResponse> {
  const params = filterStateToParams(filters);
  if (personId != null && (patientInfo == null || Object.keys(patientInfo).length === 0)) {
    params.person_id = String(personId);
  }
  const body = patientInfo ? { patient_info: patientInfo } : undefined;
  const response = await apiClient.get<TrialsResponse>("/trials/", {
    params,
    data: body,
  });
  return response.data;
}

/** GET `/trials/{id}/` — detail view. */
export async function fetchTrialDetail({
  apiClient,
  trialId,
  patientInfo,
  personId,
}: {
  apiClient: AxiosInstance;
  trialId: number;
  patientInfo?: PatientInfo | null;
  personId?: string | number;
}): Promise<TrialMatch> {
  const params: Record<string, string> = {};
  if (personId != null && (patientInfo == null || Object.keys(patientInfo).length === 0)) {
    params.person_id = String(personId);
  }
  const body = patientInfo ? { patient_info: patientInfo } : undefined;
  const response = await apiClient.get<TrialMatch>(`/trials/${trialId}/`, {
    params,
    data: body,
  });
  return response.data;
}

/** GET `/countries/` — list endpoint for the country filter. */
export async function fetchCountries(
  apiClient: AxiosInstance,
): Promise<CountriesResponse> {
  const response = await apiClient.get<CountriesResponse>("/countries/");
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

/** Convert the camelCase filter state to the query-string shape EXACT's
 *  view expects. The mapping is 1:1 with `study_preferences_from_query_params`
 *  in `trials/services/study_preferences.py`. */
function filterStateToParams(filters?: FilterState): Record<string, string> {
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
