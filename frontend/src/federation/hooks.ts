// TanStack Query hooks for the EXACT API. Keys are stable so multiple
// `TrialMatches` instances mounted in the same host share the cache
// (e.g. mounting two filtered views with the same patient doesn't
// re-request).
import {
  keepPreviousData,
  useQuery,
  type UseQueryResult,
} from "@tanstack/react-query";
import type { AxiosInstance } from "axios";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { fetchFormSettings, fetchTrialDetail, fetchTrials } from "./api";
import type { TrialId, TrialStateAdapter } from "./state";
import type {
  FilterState,
  PatientInfo,
  TrialDetailResponse,
  TrialsResponse,
} from "./types";

/** The bookmarked / registered ids, or nothing while there is no adapter.
 *
 *  Keyed on the PATIENT, not on the adapter object. Deliberately: a host
 *  that builds its adapter inline — `state={createPromopState(...)}`, the
 *  obvious way to write it — hands over a new object every render, and an
 *  identity-keyed cache would refetch on each one. The cost of that choice
 *  is that swapping transports for the SAME patient keeps the previous
 *  adapter's ids until they go stale; a host doing that should change the
 *  patient key or remount.
 */
export function useStateIds(
  state: TrialStateAdapter | undefined,
  kind: "favorites" | "registered",
  key: string,
): UseQueryResult<TrialId[]> {
  return useQuery({
    queryKey: ["exact-state-ids", kind, key],
    queryFn: () =>
      kind === "favorites"
        ? state!.listFavoriteIds()
        : state!.listRegisteredIds(),
    enabled: state != null,
    staleTime: 30_000,
  });
}

/** Toggle one trial's bookmark or registration.
 *
 *  Invalidates the id lists rather than patching them by hand: the lists
 *  drive a tab count and a tab's contents, and a hand-patched cache that
 *  drifts from the server shows a Favorites tab that disagrees with the
 *  bookmark on the card. The trial list itself is invalidated too, because
 *  on the Favorites tab the row set IS the id list.
 */
export function useSetTrialState(
  state: TrialStateAdapter | undefined,
  kind: "favorites" | "registered",
  key: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ trialId, on }: { trialId: TrialId; on: boolean }) =>
      kind === "favorites"
        ? state!.setFavorite(trialId, on)
        : state!.setRegistered(trialId, on),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exact-state-ids", kind, key] });
      queryClient.invalidateQueries({ queryKey: ["exact-trials"] });
    },
  });
}

interface UseTrialsArgs {
  apiClient: AxiosInstance;
  patientInfo?: PatientInfo | null;
  personId?: string | number;
  filters?: FilterState;
  /** 1-indexed page. */
  page?: number;
  /** Rows per page. */
  limit?: number;
  /** Narrow to these ids; `[]` means "none", not "no filter". */
  trialIds?: string[];
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
  page = 1,
  limit,
  trialIds,
  enabled = true,
}: UseTrialsArgs): UseQueryResult<TrialsResponse> {
  return useQuery({
    queryKey: [
      "exact-trials",
      personId ?? null,
      patientInfo ?? null,
      filters ?? null,
      page,
      limit ?? null,
      // `?? null` rather than a spread or a truthiness test: `[]` and
      // `undefined` are different questions and must be different keys.
      trialIds ?? null,
    ],
    queryFn: () =>
      fetchTrials({ apiClient, patientInfo, personId, filters, page, limit, trialIds }),
    // Paged, not infinite: CB paginates by number and so does this now, and
    // an infinite list cannot show per-tab totals or jump to a page. Previous
    // data is kept across page/filter changes so the list does not blank out
    // between requests — CB does the same (`keepPreviousData` in useTrials).
    placeholderData: keepPreviousData,
    // A request for a page past the end is a 404 from DRF's paginator, and
    // it will 404 again on every retry. Without this the default three
    // retries spend ~7s showing dimmed rows and "Updating…" before the
    // error commits and the list can recover to a page that exists.
    retry: (failureCount, error) =>
      (error as { response?: { status?: number } })?.response?.status !== 404 &&
      failureCount < 3,
    enabled: enabled && (patientInfo != null || personId != null),
    staleTime: 30_000,
  });
}

interface UseTrialDetailArgs {
  apiClient: AxiosInstance;
  trialId: number | string;
  patientInfo?: PatientInfo | null;
  personId?: string | number;
  filters?: FilterState;
  enabled?: boolean;
}

export function useTrialDetail({
  apiClient,
  trialId,
  patientInfo,
  personId,
  filters,
  enabled = true,
}: UseTrialDetailArgs): UseQueryResult<TrialDetailResponse> {
  return useQuery({
    queryKey: [
      "exact-trial-detail",
      trialId,
      personId ?? null,
      patientInfo ?? null,
      filters ?? null,
    ],
    queryFn: () => fetchTrialDetail({ apiClient, trialId, patientInfo, personId, filters }),
    enabled: enabled && trialId != null,
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
