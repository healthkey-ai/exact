// TanStack Query hooks for the EXACT API. Keys are stable so multiple
// `TrialMatches` instances mounted in the same host share the cache
// (e.g. mounting two filtered views with the same patient doesn't
// re-request).
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import type { AxiosInstance } from "axios";

import { fetchFormSettings, fetchTrials } from "./api";
import type {
  FilterState,
  PatientInfo,
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
