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
import { useEffect, useRef, useState } from "react";

import { filtersToStore, sanitizeStoredFilters } from "./filters";

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

/** How long a change waits before it is written.
 *
 *  The panel's text inputs fire on every keystroke, and this is a network
 *  write: unthrottled, "myeloma" is seven PATCHes. Matches the debounce the
 *  same fields already use for the search itself. */
const SAVE_DEBOUNCE_MS = 500;

export interface FilterPersistence {
  /** The patient's stored filters, `undefined` until they are known. */
  stored?: FilterState;
  /** The first read is still outstanding. The list waits on it — otherwise
   *  one request goes out unfiltered and a second follows with the saved
   *  filters, which is the flicker the derived `country` exists to avoid. */
  isPending: boolean;
  /** The read failed, so the reader's saved filters are not applied and the
   *  panel is showing the defaults instead. Worth saying. */
  unavailable: boolean;
  /** A save was refused. */
  failed: boolean;
  /** Record a change. Debounced, and serialised behind whatever is already
   *  on the wire. */
  save(filters: FilterState): void;
  /** Clear the stored filters. */
  reset(): void;
}

/** Read and write the patient's saved filters.
 *
 *  The writes are serialised, not merely debounced. The debounce only
 *  collapses changes inside its own window: type, wait it out, type again
 *  while that PATCH is still travelling, and two are in flight — applied in
 *  whatever order they arrive, so the older set can be the one that sticks.
 *  One write at a time with the newest queued behind it makes the last
 *  change the last write by construction. (CB's filter panel carries the
 *  same machinery for the same reason.)
 */
/** One write, carrying everything it needs to finish on its own.
 *
 *  Including the ADAPTER it was made for. An adapter is bound to one
 *  person — `createPromopState` bakes the `person_id` in at construction —
 *  so it, not the component's current props, is what says where a write
 *  belongs. A write that outlives the patient on screen then still goes to
 *  the right row; checked against the current key instead, a reset queued
 *  behind a save was dropped when the host swapped patients, leaving the
 *  filters that reset was clearing.
 *
 *  Every write is ABSOLUTE — a save carries the whole filter set, a reset
 *  carries the empty one — which is why a single queue slot is enough: the
 *  newest write says everything the older ones were going to say. */
type PendingWrite =
  | { kind: "save"; filters: FilterState; key: string; adapter: TrialStateAdapter }
  | { kind: "reset"; key: string; adapter: TrialStateAdapter };

/** Whether an adapter can do the whole of stored filters — read, write and
 *  clear.
 *
 *  All three, deliberately. The interface is a compile-time contract and a
 *  host may be plain JavaScript, or half-upgraded; with only some of the
 *  methods the feature does not degrade into a smaller one, it degrades
 *  into a wrong one — filters that load and then cannot be changed, or a
 *  Reset that throws. Missing any, the panel simply works locally and says
 *  so.
 *
 *  Same reasoning as `canReadAdvanced`, which guards the control that would
 *  otherwise overwrite a status it could not read. */
export function canPersistFilters(state?: TrialStateAdapter): boolean {
  return (
    typeof state?.getPreferences === "function" &&
    typeof state?.savePreferences === "function" &&
    typeof state?.resetPreferences === "function"
  );
}

export function useFilterPersistence(
  state: TrialStateAdapter | undefined,
  key: string,
  /** What the host asked for, so it is not saved as the patient's own. */
  hostFilters: FilterState = {},
): FilterPersistence {
  const queryClient = useQueryClient();
  const queryKey = ["exact-state-filters", key];
  const query = useQuery({
    queryKey,
    queryFn: async () => sanitizeStoredFilters(await state!.getPreferences()),
    enabled: state != null && canPersistFilters(state),
    // No retries. The whole trial list waits on this read — otherwise the
    // opening search goes out with the defaults and a second one follows
    // with the saved filters — and under the host's default three retries
    // with backoff a PROMOP outage held every tab on "Loading trials…" for
    // seven seconds. One attempt; if it fails the panel shows the defaults
    // and says so, and the search runs.
    retry: false,
    // Never refetched. The stored filters are adopted as what the panel
    // shows before the reader touches it; a background refetch landing
    // mid-session would move the controls under their hands. What that
    // costs is that this cache has to be kept true by hand — see the write
    // path, which puts each successful write into it. Without that, a host
    // sharing one QueryClient across a route change remounts the remote
    // onto the pre-write answer, and a saved filter quietly disappears.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  // Keyed, like the bookmark write record: a rejection under one patient is
  // not an error message for the next. (`unavailable` gets this for free —
  // it is `isError` on a query whose key already carries the patient.)
  const [failedFor, setFailedFor] = useState<string | null>(null);
  const setFailed = (write: PendingWrite | null, value: boolean) =>
    setFailedFor(value ? (write?.key ?? null) : null);
  /** The debounce: the timer and the write it is holding, together.
   *
   *  One ref rather than two, because "there is a timer" and "there is a
   *  payload" were the same fact kept in two places — true by convention,
   *  and one edit away from a flush that sends a write nobody scheduled. */
  const pending = useRef<{
    timer: ReturnType<typeof setTimeout>;
    write: PendingWrite;
  } | null>(null);
  const inFlight = useRef(false);
  /** Patients whose reset has been issued and not yet come back.
   *
   *  `query.data` still holds the pre-reset set until the write lands, and
   *  that is what tells a save to keep a value the host happens to agree
   *  with. So an edit made while a reset was in flight rebuilt exactly the
   *  preference the reset was clearing, and wrote it back afterwards. */
  const resetting = useRef<string[]>([]);
  /** Waiting writes, at most one per patient.
   *
   *  Not a single slot. A write supersedes an earlier one only within one
   *  patient's preferences: B's filters say nothing about A's. Sharing one
   *  slot, a reset for A queued behind A's in-flight save was overwritten
   *  by B's next edit, and A kept on the server exactly the filters they
   *  had just cleared.
   *
   *  Grouped by `key`, not by adapter. Those differ: a host that builds its
   *  adapter inline — `state={createPromopState(...)}`, the obvious way to
   *  write it — hands over a NEW object every render, all of them the same
   *  person. `key` is the component's answer to "same patient"; the adapter
   *  is how a write reaches them. Key groups, adapter delivers. */
  const queued = useRef<PendingWrite[]>([]);
  // Read through refs inside callbacks that outlive the render that made
  // them: a debounced write fires after the host may have swapped patients,
  // and the adapter it should use is the current one.
  // The unmount flush closes over the FIRST render's values (its effect has
  // an empty dependency list), so the client it writes the result into is
  // read through a ref. Written in an effect, not during render: React may
  // abandon a render, and a ref written there can hold a value that never
  // commits. Every reader of it runs from a user event or a timer, so it is
  // current by then.
  const clientRef = useRef(queryClient);
  useEffect(() => {
    clientRef.current = queryClient;
  });

  const run = (write: PendingWrite) => {
    const adapter = write.adapter;
    // No check against the current patient. The write carries the adapter
    // it was made for, and an adapter is bound to one person, so there is
    // no state of the world in which sending it is wrong — including after
    // the host has moved on.
    //
    // There is no generation counter either. Reset retires a superseded
    // write by clearing what holds it — the debounce, and the queue by
    // overwriting it — and every write is absolute, so a later one says
    // everything an earlier one would have. A generation check on top was a
    // second mechanism for the same thing, and provably dead.
    if (inFlight.current) {
      // Reset goes through the same queue as a save rather than straight
      // out to the adapter: unserialised, a reset and a save could be in
      // flight together, and the server applies them in whichever order
      // they arrive — the losing order clearing a filter the panel still
      // shows.
      const at = queued.current.findIndex((w) => w.key === write.key);
      if (at === -1) queued.current.push(write);
      // Same patient: absolute, so the newer write says everything the
      // older one would have.
      else queued.current[at] = write;
      return;
    }
    inFlight.current = true;
    if (write.kind === "reset" && !resetting.current.includes(write.key)) {
      resetting.current = [...resetting.current, write.key];
    }
    const stored = write.kind === "save" ? write.filters : {};
    // Through `Promise.resolve().then`, so an adapter that throws
    // SYNCHRONOUSLY becomes a rejection like any other. Called directly, the
    // throw escapes before the chain is built: `.finally` never runs,
    // `inFlight` stays true for ever, and every later write is queued behind
    // a write that already failed.
    Promise.resolve()
      .then(() =>
        write.kind === "save"
          ? adapter.savePreferences(write.filters)
          : adapter.resetPreferences(),
      )
      .then(
        () => {
          setFailed(write, false);
          // Under the key the write was made for, not the current one.
          clientRef.current.setQueryData(
            ["exact-state-filters", write.key],
            stored,
          );
        },
        () => setFailed(write, true),
      )
      .finally(() => {
        inFlight.current = false;
        if (write.kind === "reset") {
          resetting.current = resetting.current.filter((k) => k !== write.key);
        }
        const next = queued.current.shift();
        if (next) run(next);
      });
  };

  /** Empty the debounce, keeping what it held if it was somebody else's.
   *
   *  In one place because both callers need it and the two drifted: `save`
   *  flushed the other patient's write and `reset` simply dropped it, so a
   *  change made seconds earlier vanished if the next reader happened to
   *  click Reset. A write waiting out its debounce belongs to the reader
   *  who made it and carries the adapter that reaches them; only the same
   *  patient's is superseded, for whom the newer write says everything. */
  const clearPending = (forKey: string) => {
    const waiting = pending.current;
    if (!waiting) return;
    clearTimeout(waiting.timer);
    pending.current = null;
    if (waiting.write.key !== forKey) run(waiting.write);
  };

  const save = (filters: FilterState) => {
    // A save replaces the stored set whole. Writing one field while the
    // rest are unknown — the read still in flight, or failed — would
    // discard every filter the patient had saved, on the strength of a
    // panel that is showing the defaults because we could not read them.
    if (state == null || !query.isSuccess) return;
    const write: PendingWrite = {
      kind: "save",
      filters: filtersToStore(
        filters,
        hostFilters,
        // Nothing is "already stored" while a reset for this patient is on
        // its way: the cache still says otherwise, and trusting it here
        // rebuilds the very preference the reset is clearing.
        resetting.current.includes(key) ? {} : query.data ?? {},
      ),
      key,
      adapter: state,
    };
    clearPending(key);
    pending.current = {
      write,
      timer: setTimeout(() => {
        pending.current = null;
        run(write);
      }, SAVE_DEBOUNCE_MS),
    };
  };

  const reset = () => {
    // Gated on the read exactly as `save` is, and NOT because clearing
    // needs to know what was there — because the banner shown when the read
    // fails says nothing will be written. Reset writing anyway would make
    // that a lie in the most expensive direction: it would destroy the
    // stored set we had just admitted we could not read.
    if (state == null || !query.isSuccess) return;
    // The debounce is dropped, payload and all: a write scheduled a moment
    // ago would otherwise be flushed on unmount and put back the filters it
    // carries, after the reset.
    //
    // Nothing clears `queued` here. A queued write for THIS patient is
    // superseded by the call below, which replaces the entry for this
    // adapter; entries for other patients are not ours to drop.
    clearPending(key);
    if (!resetting.current.includes(key)) {
      resetting.current = [...resetting.current, key];
    }
    run({ kind: "reset", key, adapter: state });
  };

  // A pending change is sent on the way out rather than dropped: the reader
  // typed it, and a host unmounting the remote — a route change, a tab —
  // is not them changing their mind.
  useEffect(
    () => () => {
      const scheduled = pending.current;
      if (!scheduled) return;
      clearTimeout(scheduled.timer);
      pending.current = null;
      run(scheduled.write);
    },
    // Mount/unmount only; everything it reads is a ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return {
    stored: query.data,
    // A DISABLED query stays `pending` for ever — so an adapter that cannot
    // answer at all held the whole trial list on "Loading trials…"
    // permanently, which is the same trap the state tabs fell into.
    isPending: canPersistFilters(state) && query.isPending,
    // Cannot be asked is as unavailable as asked-and-refused: the panel is
    // showing the defaults and nothing will be written either way, so the
    // reader is owed the same sentence.
    unavailable: state != null && (query.isError || !canPersistFilters(state)),
    failed: failedFor === key,
    save,
    reset,
  };
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
