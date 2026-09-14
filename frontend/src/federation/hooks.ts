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

import {
  fetchFormSettings,
  fetchTrialDetail,
  fetchTrials,
  fetchTrialsGraph,
} from "./api";
import { canEditFields } from "./state";
import { PatientFieldWriter } from "./patientWriter";
import type { AdvancedStatus, TrialId, TrialStateAdapter } from "./state";
import type { WritableFields } from "./writable";
import type {
  FilterState,
  PatientInfo,
  TrialDetailResponse,
  TrialsGraphResponse,
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

/** What this caller may edit, for this patient.
 *
 *  Keyed on the patient, not on the trial: the answer is about the person's
 *  record and is the same on every trial page. `staleTime` is long because it
 *  moves with the vocabulary and a caller's permissions, not with anything the
 *  reader does — but it is not `Infinity`, because both of those do change and
 *  a session can outlive them.
 *
 *  Failure is left as failure rather than defaulted to `{}`. An empty
 *  descriptor and an unanswered one both end with no controls drawn, but only
 *  one of them should be retried, and telling them apart later needs the
 *  difference kept now.
 */
export function useWritableFields(
  state: TrialStateAdapter | undefined,
  key: string,
): UseQueryResult<WritableFields> {
  const can = canEditFields(state);
  return useQuery({
    queryKey: ["exact-writable-fields", key],
    queryFn: () => state!.getWritableFields!(),
    enabled: can,
    staleTime: 5 * 60_000,
  });
}

/** The queue behind inline editing, and what the page paints from it.
 *
 *  `save` returns nothing and takes no time: the reader has pressed Save, the
 *  row shows their value on trust, and the request goes out with whatever else
 *  they change in the same breath. What comes back decides what the row shows
 *  next.
 *
 *  `outstanding` is that trust, by field — the value shown until the record
 *  answers. It is retired on EVERY outcome, including a refusal: leaving it up
 *  would show the reader a value the record does not hold, which is the
 *  failure this phase exists to prevent. A refusal instead names the field in
 *  `failed`, so the row can say so.
 */
export function useQueuedPatientFields(
  state: TrialStateAdapter | undefined,
  key: string,
): {
  save: (field: string, value: unknown) => void;
  outstanding: Record<string, unknown>;
  failed: Record<string, unknown>;
} {
  const queryClient = useQueryClient();
  const [outstanding, setOutstanding] = useState<Record<string, unknown>>({});
  // Keyed by field and holding the VALUE that failed, not just the name. The
  // row shows the record's value — the truthful one — and the editor opens on
  // what the reader typed, so a refusal costs them a click and not the work.
  // PROMOP validates a PATCH as a whole inside one transaction, so a single
  // bad field takes the rest of the batch down with it; retyping three good
  // values because of a fourth is not a thing to ask of anyone.
  const [failed, setFailed] = useState<Record<string, unknown>>({});

  // Read through a ref so the writer below is not rebuilt when a host hands
  // over a new adapter object for the same patient — rebuilding would drop
  // whatever it had queued.
  const stateRef = useRef(state);
  stateRef.current = state;
  // Which patient that ref currently belongs to. Both are written during
  // render, so by the time an unmount cleanup runs after a patient switch
  // they already describe the NEW patient — which is exactly why the writer
  // below must not reach through them blindly.
  const keyRef = useRef(key);
  keyRef.current = key;
  const can = canEditFields(state);

  // What was last sent for each field, read when a refusal comes back — by
  // then the optimistic state has been retired and the value would be gone.
  const sentRef = useRef<Record<string, unknown>>({});
  // How many edits to each field have not been answered yet.
  //
  // Without this the state is keyed by field alone and knows nothing about
  // WHICH edit it belongs to: a reader who changes the same value twice while
  // the first request is out has the first answer retire the second one's
  // optimistic value — the row dropping back to the server's older number,
  // with no "Saving…" on it, until the second re-read lands. The same
  // mistake, mirrored, makes a refusal of the first blame the second's value
  // and clear a pending indicator for an edit the writer is about to send.
  //
  // So an answer only settles a field that has nothing newer waiting.
  const owed = useRef<Record<string, number>>({});
  const retire = (fields: string[]) =>
    setOutstanding((current) => {
      const next = { ...current };
      for (const field of fields) delete next[field];
      return next;
    });

  // A batch settling is what makes the match move, so the invalidation
  // belongs to the writer rather than to each field: one re-read per request,
  // not one per value.
  const settledRef = useRef<() => Promise<void>>(async () => {});
  // Fields reported in the current batch, retired together once the re-read
  // above has landed.
  const settledFields = useRef<string[]>([]);
  settledRef.current = () => {
    queryClient.invalidateQueries({ queryKey: ["exact-trials"] });
    queryClient.invalidateQueries({ queryKey: ["exact-writable-fields", key] });
    // Returned, because the optimistic values are retired when it resolves:
    // the row's own `uvalue` only changes when this lands, so retiring before
    // it would put the OLD value back on screen for a whole round trip —
    // "13 → Saving… → 12 → 13", which is the save-that-did-nothing this
    // overlay exists to prevent, moved later rather than removed.
    return queryClient.invalidateQueries({ queryKey: ["exact-trial-detail"] });
  };

  // Whose verdicts the state below belongs to. A patient switch builds a new
  // writer while the old one's flush is still in the air, and that answer
  // lands afterwards — under the new patient, about the previous one's row.
  //
  // Claimed in an EFFECT, not during render. React invokes a `useMemo`
  // factory twice under StrictMode and keeps one of the two results, so a ref
  // assigned inside the factory ends up naming the instance that was thrown
  // away — and every callback of the instance actually in use then fails its
  // own ownership check. Every entry point in this repo mounts under
  // StrictMode, so that is not an edge case, it is the behaviour: the write
  // went out, nothing was retired, no re-read was started, and "Saving…" sat
  // there for ever. An effect runs only for the instance React kept.
  const writerRef = useRef<PatientFieldWriter | null>(null);
  const writer = useMemo(() => {
    if (!can) {
      writerRef.current = null;
      return null;
    }
    // The adapter is CAPTURED here and only reached for through the ref while
    // the patient is unchanged. The two ways `state` can move need opposite
    // answers, and `useSavedFilters` settles them the same way:
    //
    //  - same patient, new adapter object (a host writing
    //    `state={createPromopState(...)}` inline, or a refreshed client): use
    //    the current one, or a write would go through an expired client.
    //  - different patient: use the captured one. This writer belongs to the
    //    previous patient and its flush carries values edited FOR them —
    //    sent through the ref they would be written into the NEXT patient's
    //    record. For a saved search that is an annoyance; for a haemoglobin
    //    it is one person's lab value in another person's chart.
    const captured = stateRef.current;
    const live = () =>
      (keyRef.current === key ? (stateRef.current ?? captured) : captured)!;
    const built: PatientFieldWriter = new PatientFieldWriter(
      (fields) => live().setPatientFields!(fields),
      {
        onSettled: (field) => {
          if (writerRef.current && writerRef.current !== built) return;
          owed.current[field] = Math.max(0, (owed.current[field] ?? 1) - 1);
          // Superseded: a newer edit to this field has not been answered yet,
          // and ITS value is what the row is showing.
          if (owed.current[field] > 0) return;
          settledFields.current.push(field);
          setFailed((current) => {
            if (!(field in current)) return current;
            const next = { ...current };
            delete next[field];
            return next;
          });
        },
        onBatchSettled: () => {
          const reported = settledFields.current;
          settledFields.current = [];
          if (writerRef.current && writerRef.current !== built) return;
          void settledRef.current().then(() => {
            if (writerRef.current && writerRef.current !== built) return;
            retire(reported);
          });
        },
        onError: (fields) => {
          if (writerRef.current && writerRef.current !== built) return;
          const settled = fields.filter((field) => {
            owed.current[field] = Math.max(0, (owed.current[field] ?? 1) - 1);
            return owed.current[field] === 0;
          });
          if (settled.length === 0) return;
          setFailed((current) => {
            const next = { ...current };
            for (const field of settled) next[field] = sentRef.current[field];
            return next;
          });
          retire(settled);
        },
      },
    );
    return built;
    // `can` rather than `state`: see above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [can, key]);

  // An edit made in the last quarter second should survive the reader
  // navigating away, or the host switching patients — which rebuilds the
  // writer and runs this cleanup against the OLD one, carrying edits made for
  // the previous patient.
  useEffect(() => {
    writerRef.current = writer;
    return () => {
      // Only if it is still ours: a later writer has already claimed it.
      if (writerRef.current === writer) writerRef.current = null;
    };
  }, [writer]);

  useEffect(() => {
    if (!writer) return;
    return () => {
      writer.flush();
    };
  }, [writer]);

  // A patient switch is not an edit: the previous patient's optimistic values
  // must not be painted over the new one's rows.
  useEffect(() => {
    setOutstanding({});
    setFailed({});
    sentRef.current = {};
    owed.current = {};
  }, [key]);

  return useMemo(
    () => ({
      save: (field: string, value: unknown) => {
        if (!writer) return;
        owed.current[field] = (owed.current[field] ?? 0) + 1;
        sentRef.current[field] = value;
        setOutstanding((current) => ({ ...current, [field]: value }));
        setFailed((current) => {
          if (!(field in current)) return current;
          const next = { ...current };
          delete next[field];
          return next;
        });
        writer.save(field, value);
      },
      outstanding,
      failed,
    }),
    [writer, outstanding, failed],
  );
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

/** The knowledge graph, fetched only once the reader opens it.
 *
 *  It is a second full matcher run over the same search — every trial's
 *  eligibility table, computed per trial — so it is not something to have
 *  ready just in case. `enabled` is the open state of the panel.
 */
export function useTrialsGraph({
  apiClient,
  patientInfo,
  personId,
  filters,
  trialIds,
  limit,
  enabled,
}: {
  apiClient: AxiosInstance;
  patientInfo?: PatientInfo | null;
  personId?: string | number | null;
  filters?: FilterState;
  trialIds?: string[];
  limit?: number;
  enabled: boolean;
}): UseQueryResult<TrialsGraphResponse> {
  return useQuery({
    queryKey: [
      "exact-trials-graph",
      personId ?? null,
      patientInfo ?? null,
      filters ?? null,
      // In the key as well as the request: a state tab narrows by ids alone,
      // so without this its graph and the default tab's share a key and the
      // wrong one is served from cache.
      trialIds ?? null,
      limit ?? null,
    ],
    queryFn: () =>
      fetchTrialsGraph({ apiClient, patientInfo, personId, filters, trialIds, limit }),
    enabled,
    staleTime: 60_000,
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
  /** `applied` is false when the reader edited while the load was in flight:
   *  take the keys, leave the values. */
  onLoad: (saved: FilterState, applied: boolean) => void,
): {
  persist: (filters: FilterState) => void;
  reset: () => void;
  /** The last write did not land. */
  failed: boolean;
  /** Which attempt this is. Changes whenever the reader behind the panel
   *  changes, so a view keyed on it is rebuilt rather than reused. */
  epoch: object;
  /** True while the stored set is still being read, and for at most
   *  `SAVED_FILTERS_GRACE_MS` after — the list must not be hostage to the
   *  preferences service. */
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

  // A failed write is not a reason to fail the search, but it is a reason to
  // say something: the transport REFUSES to write when it cannot read what it
  // would be replacing, and without this the reader's edit simply vanishes —
  // they come back tomorrow and their filters are last week's.
  const [failed, setFailed] = useState(false);
  // Which writer's verdict the message belongs to. A patient switch builds a
  // new one while the old one's flush is still in the air, and that flush
  // lands afterwards — under the new patient, about the previous one's row.
  const writerRef = useRef<PreferenceWriter | null>(null);
  const writer = useMemo(() => {
    const built: PreferenceWriter = new PreferenceWriter(transport, {
      onError: () => {
        if (writerRef.current === built) setFailed(true);
      },
      // Cleared when a write LANDS, not when one is issued. Writes queue, so
      // an edit made while a failing one is in flight would clear the message
      // optimistically and then never restore it — or, the other way round,
      // leave it standing over filters that did in fact save.
      onSuccess: () => {
        if (writerRef.current === built) setFailed(false);
      },
    });
    writerRef.current = built;
    return built;
  }, [transport]);

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
    // The message described the PREVIOUS patient's row. Left standing it reads
    // as a failure to save this patient's filters, which nobody has tried yet.
    setFailed(false);
    // No cleanup: a cap belonging to a replaced writer can only fire into
    // `release`, which ignores it. Clearing it as well would be a second
    // mechanism for the same thing, and untestable because the first already
    // covers it.
    setTimeout(() => release(writer), SAVED_FILTERS_GRACE_MS);
  }, [writer]);

  useEffect(() => {
    let cancelled = false;
    void transport
      .get()
      .then((raw) => {
        if (cancelled) return;
        // Validated here rather than in each transport, so the adapter and
        // the localStorage fallback are held to the same line: what comes
        // back is a CLAIM about filters, not a `FilterState`.
        const saved = sanitizeStoredFilters(raw);
        // Nothing saved is the common case and must not clobber the filters
        // the host seeded, so an empty object is treated as "no opinion".
        if (Object.keys(saved).length === 0) return;
        // `applied: false` when the reader edited while this was in flight:
        // the VALUES are dropped, because merging them over an edit visibly
        // reverts what they just did. The KEYS are reported either way, and
        // that distinction is load-bearing — ownership is what makes a
        // cleared filter clearable, and the transport has already read these
        // keys. An earlier version of this branch called `transport.forget()`
        // here instead, which left them in every payload with no way to
        // remove them.
        onLoadRef.current(saved, !editedRef.current);
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
      failed,
      pending,
      epoch: writer,
    }),
    [writer, failed, pending],
  );
}
