// TanStack Query hooks for the EXACT API. Keys are stable so multiple
// `TrialMatches` instances mounted in the same host share the cache
// (e.g. opening a detail view doesn't re-request the list).
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import type { AxiosInstance } from "axios";

import { fetchCountries, fetchFormSettings, fetchTrialDetail, fetchTrials } from "./api";
import type {
  CountriesResponse,
  FilterState,
  PatientInfo,
  TrialMatch,
  TrialsResponse,
} from "./types";

interface UseTrialsArgs {
  apiClient: AxiosInstance;
  patientInfo?: PatientInfo | null;
  personId?: string | number;
  filters?: FilterState;
  /** Skip the query until the host has a patient context. Without
   *  patient context the response would be a public/unscoped trial
   *  list — usually not what a TrialMatches mount wants. */
  enabled?: boolean;
}

export function useTrials({
  apiClient,
  patientInfo,
  personId,
  filters,
  enabled = true,
}: UseTrialsArgs): UseQueryResult<TrialsResponse> {
  return useQuery({
    queryKey: ["exact-trials", personId ?? null, patientInfo ?? null, filters ?? null],
    queryFn: () => fetchTrials({ apiClient, patientInfo, personId, filters }),
    enabled: enabled && (patientInfo != null || personId != null),
    staleTime: 30_000,
  });
}

interface UseTrialDetailArgs {
  apiClient: AxiosInstance;
  trialId: number | null;
  patientInfo?: PatientInfo | null;
  personId?: string | number;
}

export function useTrialDetail({
  apiClient,
  trialId,
  patientInfo,
  personId,
}: UseTrialDetailArgs): UseQueryResult<TrialMatch> {
  return useQuery({
    queryKey: ["exact-trial-detail", trialId, personId ?? null, patientInfo ?? null],
    queryFn: () => fetchTrialDetail({ apiClient, trialId: trialId!, patientInfo, personId }),
    enabled: trialId != null,
    staleTime: 60_000,
  });
}

export function useCountries(
  apiClient: AxiosInstance,
): UseQueryResult<CountriesResponse> {
  return useQuery({
    queryKey: ["exact-countries"],
    queryFn: () => fetchCountries(apiClient),
    staleTime: 5 * 60_000,
  });
}

export function useFormSettings(
  apiClient: AxiosInstance,
  diseaseCode?: string,
): UseQueryResult<Record<string, { options: { value: string; label: string }[] }>> {
  return useQuery({
    queryKey: ["exact-form-settings", diseaseCode ?? null],
    queryFn: () => fetchFormSettings(apiClient, diseaseCode),
    staleTime: 5 * 60_000,
  });
}
