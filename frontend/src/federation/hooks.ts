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
import { useEffect, useMemo, useRef, useState } from "react";

import { sanitizeStoredFilters } from "./filters";
import {
  PreferenceWriter,
  adapterPreferences,
  localStoragePreferences,
} from "./preferences";

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
  // Off by default at the call site's discretion: the only caller today wants
  // the option TITLES for a panel that most trials never render, and fetching
  // a catalog for every detail view to label nothing would be a request per
  // trial for no reader's benefit.
  enabled = true,
): UseQueryResult<Record<string, { options: { value: string; label: string }[] }>> {
  return useQuery({
    queryKey: ["exact-form-settings", diseaseCode ?? null],
    queryFn: () => fetchFormSettings(apiClient, diseaseCode),
    staleTime: 5 * 60_000,
    enabled,
  });
}

/** Saved search filters: load them once, and route every write through the
 *  queue in `preferences.ts`.
 *
 *  Keyed on the PATIENT, not on the adapter object — same reasoning as
 *  `useStateIds`: a host that builds `state={createPromopState(...)}` inline
 *  hands over a new object every render, and rebuilding the writer on each one
 *  would lose whatever it had queued. A host that genuinely replaces the
 *  adapter for the same patient — a refreshed auth client, say — is still
 *  honoured, because calls route through whatever adapter is current for
 *  THIS key rather than through the one captured when the writer was built.
 *
 *  With no adapter this falls back to `localStorage` rather than doing
 *  nothing. The filter panel is the same panel either way, and a panel that
 *  forgets on reload is a worse answer than a per-browser one.
 */
/** How long the trial search waits for the saved filters before going
 *  ahead without them.
 *
 *  Short: it is a single small GET against a service the host has already
 *  authenticated, so the common case is a few milliseconds and the reader
 *  never sees the wait. Long enough that the common case actually fits
 *  inside it, which is the whole point — one search instead of two. */
export const SAVED_FILTERS_GRACE_MS = 300;

export function useSavedFilters(
  state: TrialStateAdapter | undefined,
  key: string,
  onLoad: (saved: FilterState) => void,
): {
  persist: (filters: FilterState) => void;
  reset: () => void;
  /** Which attempt this is. Changes whenever the reader behind the panel
   *  changes — a new patient, or a host handing over an adapter for the
   *  same one — so a caller can key its own per-attempt state on it rather
   *  than re-deriving "is this still the same reader" from the props. */
  epoch: object;
  /** True while the stored set is still being read, and for at most
   *  `SAVED_FILTERS_GRACE_MS`.
   *
   *  The caller holds the trial search back on it. Without that the opening
   *  search goes out with the defaults and a second one follows once the
   *  saved set lands — two matcher runs, and a flash of unfiltered results
   *  for a reader who had explicitly narrowed them.
   *
   *  Capped, because the list must not be hostage to the preferences
   *  service: a read that is merely SLOW gives the reader an unfiltered
   *  list after the grace period and a corrected one when it arrives, which
   *  is what happened every time before this gate existed. A read that
   *  fails releases it immediately. */
  pending: boolean;
} {
  const hasAdapter = state != null;
  // Read through a ref so the memo below does not rebuild on every render —
  // a host writing `state={createPromopState(...)}` inline hands over a new
  // object each time, and rebuilding would drop whatever the writer had queued.
  const stateRef = useRef(state);
  stateRef.current = state;
  // Which patient the ref above currently belongs to. Both refs are written
  // during render, so by the time an unmount cleanup runs after a patient
  // switch they already describe the NEW patient.
  const keyRef = useRef(key);
  keyRef.current = key;

  const transport = useMemo(() => {
    // Each call picks its adapter rather than closing over one, because the
    // two ways `state` can change need opposite answers:
    //
    //  - same patient, new adapter object (an inline `createPromopState(...)`,
    //    or a genuinely refreshed client): use the current one. Rebuilding the
    //    memo instead would drop whatever the writer had queued, and closing
    //    over the old one would write through an expired client.
    //  - different patient: use the captured one. This transport belongs to
    //    the previous patient, and its writer's unmount flush carries filters
    //    edited FOR that patient — sending them through the ref would file
    //    them under the next patient's preferences.
    const captured = stateRef.current;
    const live = () =>
      (keyRef.current === key ? (stateRef.current ?? captured) : captured)!;
    return captured
      ? adapterPreferences({
          getPreferences: () => live().getPreferences(),
          savePreferences: (f) => live().savePreferences(f),
          resetPreferences: () => live().resetPreferences(),
        })
      : localStoragePreferences(key);
    // `hasAdapter` rather than `state`: see above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasAdapter, key]);

  const writer = useMemo(() => new PreferenceWriter(transport), [transport]);

  const onLoadRef = useRef(onLoad);
  onLoadRef.current = onLoad;

  // Whether the reader has touched the filters since this writer was built.
  // A slow `getPreferences()` that resolves afterwards must not merge the
  // stored set over an edit made while it was in flight — that visibly
  // reverts what they just did, and only on a slow connection, which is the
  // hardest kind of bug to be told about.
  const editedRef = useRef(false);
  // The gate is owned by the WRITER, and re-raised DURING the render that
  // replaces it rather than in an effect afterwards.
  //
  // The writer, not the patient key: the two come apart when a host gains
  // an adapter for the same patient — the panel was on `localStorage` a
  // moment ago, the key never changed, and the gate would stay open while
  // PROMOP's slower read was still in flight. It is the reader the answer
  // is coming from that decides, and each writer has exactly one read.
  //
  // During render, because an effect is a render too late: by then the
  // query observer has been handed the new key and has already fired for
  // it, which is the very double request this exists to collapse.
  const [gate, setGate] = useState<{ owner: object; pending: boolean }>({
    owner: writer,
    pending: true,
  });
  if (gate.owner !== writer) setGate({ owner: writer, pending: true });
  // No `owner !== writer ? true : …` fallback beside it: React re-runs the
  // component before committing the update above, so the render anybody
  // observes already has the new owner. A fallback would be a second
  // mechanism for the same thing — and, as it turned out, the one the
  // tests were actually exercising.
  const pending = gate.pending;
  // Only the current writer's read may open it.
  //
  // The read path is already guarded — a torn-down effect sets `cancelled`
  // — so what this actually covers is the CAP, which is not cleared when
  // its writer is replaced: without it, one writer's cap opens the gate of
  // a writer whose own answer has not arrived. The list then shows rows for
  // the previous reader's filters until the successor's cap comes round.
  const release = (owner: object) =>
    setGate((current) =>
      current.owner === owner ? { owner, pending: false } : current,
    );
  useEffect(() => {
    editedRef.current = false;
    // No cleanup: a cap belonging to a replaced writer fires into
    // `release`, which ignores it. Clearing it as well would be a second
    // mechanism for the same thing, and untestable beside the first.
    setTimeout(() => release(writer), SAVED_FILTERS_GRACE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [writer]);

  useEffect(() => {
    let cancelled = false;
    void transport
      .get()
      .then((raw) => {
        // Validated here rather than in each transport, so the PROMOP row
        // and the localStorage fallback are held to the same rule with one
        // line. What the transports then do with an unrecognised key
        // differs and is deliberately not equalised here: the PROMOP one
        // clears it on the next save (it remembers the raw keys it read),
        // while the localStorage one leaves it on disk, where it is inert
        // but permanent.
        const saved = sanitizeStoredFilters(raw);
        // Either way the load is NOT applied, which leaves the writer holding
        // a set that is a strict SUBSET of what is stored — the reader's one
        // edit. Telling the transport to forget what it read keeps the next
        // save additive; without it that save reads as "these are all the
        // filters there are" and deletes saved filters the reader never even
        // saw. `cancelled` needs it just as much as an edit does: an unmount
        // runs the flush against this same transport, and the read it is
        // queued behind seeds the transport on its way through.
        if (cancelled || editedRef.current) {
          transport.forget();
          return;
        }
        // Nothing saved is the common case and must not clobber the filters
        // the host seeded, so an empty object is treated as "no opinion".
        if (Object.keys(saved).length === 0) return;
        onLoadRef.current(saved);
      })
      .catch(() => {
        // Unreachable saved filters are not a reason to fail the search.
      })
      // `.finally`, so a read that FAILS opens the gate too — one that
      // only opens on success holds the list for ever the day PROMOP is
      // unreachable.
      //
      // But not a CANCELLED one. Ownership cannot tell those apart:
      // StrictMode double-invokes the mount effect for the same writer, so
      // the first read — the one whose effect has already been torn down —
      // would open the gate belonging to the second, still in flight. Every
      // entry point in this repo mounts under StrictMode, so that is the
      // configuration the next person debugging this will be looking at.
      .finally(() => {
        if (!cancelled) release(writer);
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  // An edit made in the last few hundred milliseconds should survive the
  // reader navigating away — or the host switching patients, which rebuilds
  // the writer and runs this cleanup against the OLD transport.
  useEffect(() => () => writer.flush(), [writer]);

  // An unmount is not the only way to leave. A reload, a tab close or a host
  // full-page navigation never unmounts anything, so the debounced write of
  // the filter just set — the one the reader is most likely to expect back —
  // is exactly the one that would be lost. `pagehide` covers the bfcache case
  // that `beforeunload` does not; `visibilitychange` covers mobile, where a
  // backgrounded tab may never fire either.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const leave = () => {
      // `visibilitychange` fires on the way BACK too. Flushing then would
      // cancel the debounce for an edit still in progress — a half-typed
      // title persisted, and a second request when the reader finishes.
      if (document.visibilityState !== "hidden") return;
      writer.flush();
    };
    const hide = () => writer.flush();
    window.addEventListener("pagehide", hide);
    document.addEventListener("visibilitychange", leave);
    return () => {
      window.removeEventListener("pagehide", hide);
      document.removeEventListener("visibilitychange", leave);
    };
  }, [writer]);

  return useMemo(
    () => ({
      persist: (filters: FilterState) => {
        editedRef.current = true;
        writer.save(filters);
      },
      reset: () => {
        editedRef.current = true;
        writer.reset();
      },
      pending,
      epoch: writer,
    }),
    [writer, pending],
  );
}
