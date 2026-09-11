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
import type { AdvancedStatus, TrialId, TrialStateAdapter } from "./state";
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
 *  Applies the change to the cached id list AND invalidates it. Both,
 *  because each alone is wrong in its own way.
 *
 *  Invalidating alone leaves a window where the write has succeeded and the
 *  list has not been re-read yet, and every control is painted from that
 *  list: the reader saw "Saving…" turn back into "I'm Interested", which
 *  reads as "it didn't take" and invites the second click the pending guard
 *  exists to prevent. Holding the mutation open until the re-read returns
 *  closes the window but pays for it with the whole round trip — with the
 *  host's default retries, up to some seven seconds of a disabled button
 *  after the write already succeeded, and a failed re-read then leaves the
 *  control contradicting the record anyway.
 *
 *  Patching alone is the bug the first version of this comment warned
 *  about: a cache that drifts from the server shows a Favorites tab that
 *  disagrees with the star on the card. So the patch answers immediately
 *  and the invalidation reconciles a round trip later; drift is bounded to
 *  that round trip rather than lasting until something else refetches.
 *
 *  The trial list is invalidated too: on the Favorites tab the row set IS
 *  the id list.
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
    onSuccess: (_result, { trialId, on }) => {
      const idsKey = ["exact-state-ids", kind, key];
      // Only an existing list is patched. With nothing cached there is
      // nothing to be consistent with, and the invalidation below will
      // fetch the truth.
      queryClient.setQueryData<TrialId[]>(idsKey, (previous) => {
        if (previous == null) return previous;
        if (!on) return previous.filter((id) => id !== trialId);
        return previous.includes(trialId) ? previous : [...previous, trialId];
      });
      queryClient.invalidateQueries({ queryKey: idsKey });
      queryClient.invalidateQueries({ queryKey: ["exact-trials"] });
    },
  });
}

/** Whether an adapter can answer the advanced-status question at all.
 *
 *  The method is required by the interface, but the interface is a
 *  compile-time contract and a host may be plain JavaScript — or an older
 *  build of one. Missing, calling it throws a TypeError inside the query,
 *  which is a confusing route to the right outcome; checked, the outcome is
 *  the same and it is deliberate. Either way the register control is
 *  withheld, because it is the control that would overwrite the status this
 *  answers about. */
export function canReadAdvanced(state?: TrialStateAdapter): boolean {
  return typeof state?.listAdvancedEnrollments === "function";
}

/** The trials a study team has already moved past "registered".
 *
 *  Read on the same key as the id lists, and for the same reason: it
 *  decides whether a control that WRITES is drawn at all. */
export function useAdvancedEnrollments(
  state: TrialStateAdapter | undefined,
  key: string,
): UseQueryResult<Record<TrialId, AdvancedStatus>> {
  return useQuery({
    queryKey: ["exact-state-advanced", key],
    queryFn: () => state!.listAdvancedEnrollments(),
    enabled: state != null && canReadAdvanced(state),
    staleTime: 30_000,
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
