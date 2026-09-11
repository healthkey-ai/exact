// Federated `./TrialMatches` export (#104, part of #101). Renders the
// patient's trial matches grouped by `matchingType`, with a filter bar,
// inline detail view, and host-agnostic axios injection. The host
// supplies either `patientInfo` (inline payload — matches the existing
// CB contract) or `personId` (CTOMOP federation path added in #102).
import { useEffect, useMemo, useState, useRef } from "react";

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setDebounced(value), delay);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [value, delay]);
  return debounced;
}
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FilterPanel } from "./FilterPanel";
import { TrialCard } from "./TrialCard";
import { TrialDetailPage } from "./TrialDetailPage";
import { Pagination } from "./Pagination";
import { SortControl } from "./SortControl";
import { Tabs } from "./Tabs";
import { hasInlinePatient } from "./api";
import {
  baselineFilters,
  countActiveFilters,
  countryFor,
  userOwnedFilters,
} from "./filters";
import { MAX_TRIAL_IDS } from "./state";
import {
  DEFAULT_SORT,
  PAGE_SIZE,
  tabValueForType,
  tabsFor,
  type TabValue,
} from "./listChrome";
import {
  canReadAdvanced,
  useAdvancedEnrollments,
  useSavedFilters,
  useSetTrialState,
  useStateIds,
  useTrials,
} from "./hooks";
import { injectStyles } from "./injectStyles";
import type { FilterState, TrialMatch, TrialMatchesProps } from "./types";

type StateKind = "favorites" | "registered";
/** Which trials have a write in flight, and which have one that failed. */
interface WriteState {
  pending: string[];
  failed: string[];
}

function TrialMatchesInner({
  apiClient,
  patientInfo,
  personId,
  initialFilters,
  onTrialSelect,
  state,
}: Omit<TrialMatchesProps, "queryClient">) {
  useEffect(() => {
    injectStyles();
  }, []);

  const [filters, setFilters] = useState<FilterState>(initialFilters ?? {});
  const [selectedTrial, setSelectedTrial] = useState<TrialMatch | null>(null);
  // Seeded from the host's `initialFilters.type` rather than defaulted: the
  // prop is public API, and a host that mounts the remote asking for the
  // potential subset must not silently get the default tab's result set.
  const [activeTab, setActiveTab] = useState<TabValue>(() =>
    tabValueForType(initialFilters?.type),
  );
  const [sort, setSort] = useState<string>(initialFilters?.sort ?? DEFAULT_SORT);
  const [page, setPage] = useState(1);
  const [filtersOpen, setFiltersOpen] = useState(false);

  // Reset detail view when patient context changes so we don't keep a
  // stale trial open from a previous patient. We key on a stable derived
  // identifier (`personId` or the JSON-serialised payload) instead of the
  // `patientInfo` reference directly — otherwise a host that re-creates
  // the payload object on every render (the default in React without
  // `useMemo`) would collapse the detail view on every parent re-render.
  const patientInfoKey = useMemo(
    () => (patientInfo ? JSON.stringify(patientInfo) : null),
    [patientInfo],
  );
  useEffect(() => {
    setSelectedTrial(null);
    setPage(1);
  }, [personId, patientInfoKey]);

  const diseaseCode = useMemo(() => {
    const d = (patientInfo as Record<string, unknown> | null | undefined)?.["disease"];
    return typeof d === "string" ? d : undefined;
  }, [patientInfo]);

  // `country` and `trialType` are DERIVED, not stored.
  //
  // Seeding them into filter state — even during render — put a request on
  // the wire before the seeding took effect: the component suite caught two
  // requests on every mount with a patient country, the first of them
  // unscoped. A render-phase `setState` re-runs the component but does not
  // un-send what the query observer has already been told to fetch.
  //
  // `country` also no longer has a control at all — its dropdown could not
  // match anything (#430) — so storing it was storing a copy of a fact.
  // `trialType` does have one; what is derived there is only whether the
  // stored choice still applies to the patient on screen.
  const patientCountry = useMemo(() => {
    const c = (patientInfo as Record<string, unknown> | null | undefined)?.["country"];
    return typeof c === "string" && c.trim() ? c.trim() : undefined;
  }, [patientInfo]);
  const country = countryFor(patientCountry, initialFilters);

  // A trial type belongs to the PATIENT it was chosen for, not to their
  // disease. Carried into someone else it narrows their list by a choice
  // they never made — and across diseases it is worse, because the option
  // does not exist in their list at all and `by_trial_type` has no leniency
  // for a value that is not there: an empty result set from a control
  // rendering blank. Keyed on the disease this leaked between any two
  // patients who shared one.
  //
  // `hasInlinePatient` decides which prop names the patient, because it is
  // the same function the request uses to decide which one it sends.
  const patientIdentity = hasInlinePatient(patientInfo)
    ? patientInfoKey
    : personId != null
      ? String(personId)
      : null;
  // "Unclaimed" is `undefined`, NOT `null` — and the distinction is
  // load-bearing, because `null` is a real owner here: `patientIdentity` is
  // `null` for a host placeholder like `patientInfo={}`, or for the render
  // before the profile arrives, and the panel is live in that window. While
  // the two shared a sentinel, a type picked there claimed `null`, read back
  // as unclaimed, never went stale, and followed the reader into every
  // patient afterwards, across diseases included.
  //
  // Unclaimed is also how a host's `initialFilters.trialType` outlives a
  // patient arriving a render later: nobody has claimed it, so nothing makes
  // it stale.
  const [trialTypeOwner, setTrialTypeOwner] = useState<string | null | undefined>(
    undefined,
  );
  const trialTypeIsStale =
    trialTypeOwner !== undefined && trialTypeOwner !== patientIdentity;
  // A stale choice falls back to the BASELINE's type, not to nothing.
  //
  // What goes stale is the reader's own pick, which was made for one
  // patient. `initialFilters.trialType` is a different thing — a scope the
  // host set when it mounted the remote — and `baselineFilters` already
  // treats a host filter as something Reset restores rather than discards.
  // Dropping to `undefined` threw it away silently, and cost a second click
  // besides: Reset wrote the masked baseline into state while clearing the
  // owner, so the next render's unmasked baseline disagreed with what had
  // just been stored, the badge counted that disagreement and the button
  // stayed armed.
  //
  // Falling back to the baseline's value makes the two agree by
  // construction, so the baseline itself needs no mask at all.
  const trialType = trialTypeIsStale
    ? initialFilters?.trialType
    : filters.trialType;

  const effectiveFilters = useMemo(
    () => ({ ...filters, country, trialType }),
    [filters, country, trialType],
  );

  const baseline = useMemo(
    () => baselineFilters(patientCountry, initialFilters),
    [patientCountry, initialFilters],
  );
  const activeFilterCount = countActiveFilters(effectiveFilters, baseline);

  const debouncedTitle = useDebounced(filters.searchTitle, 400);
  const debouncedTreatment = useDebounced(filters.searchTreatment, 400);
  const debouncedSponsor = useDebounced(filters.sponsor, 400);
  const debouncedDistance = useDebounced(filters.distance, 400);
  const debouncedDistanceUnits = useDebounced(filters.distanceUnits, 400);
  // The bar depends on whether a host gave us somewhere to keep bookmarks.
  const tabs = useMemo(() => tabsFor(state != null), [state]);
  // A host can take the adapter away — on logout, or when reconfiguring it.
  // The tab the reader was on then stops existing, and falling back only in
  // `activeTabDef` would list the default tab's trials while no tab in the
  // bar is marked current: the reader is somewhere the UI cannot name.
  // Adjusting during render rather than in an effect, so the request that
  // goes out is the one the visible tab describes.
  if (!tabs.some((t) => t.value === activeTab)) {
    setActiveTab(tabs[0].value);
  }
  const activeTabDef = tabs.find((t) => t.value === activeTab) ?? tabs[0];

  // BOTH props, not the precedence winner.
  //
  // `patientIdentity` answers "which prop names this patient", which is the
  // right question for the matcher and the wrong one for a cache key. With
  // both supplied it resolves to the inline payload — so two readers whose
  // payload is the same minimal `{disease}` share a key, and the second one
  // is served the first one's bookmarks for as long as they stay fresh.
  // A cache key wants maximum discrimination: any difference in either prop
  // is a different key.
  const stateKey = `${personId ?? ""}|${patientInfoKey ?? ""}`;
  const favorites = useStateIds(state, "favorites", stateKey);
  const registered = useStateIds(state, "registered", stateKey);
  // Not a display concern: this is what stops the register control being
  // drawn for a trial whose enrollment a study team has already advanced,
  // where "I'm Interested" would write `registered` over `entered`.
  const advanced = useAdvancedEnrollments(state, stateKey);
  // Saved filters. Applied over the host's `initialFilters` rather than in
  // place of them: the seeded country is the baseline the reader never chose,
  // and a saved set that omits it must not silently widen the search to every
  // country. Keys the reader did save win.
  // Which fields are the reader's rather than the host's. Sticky: seeded from
  // whatever was loaded, added to on every save, and emptied by Reset. Without
  // it a saved field that happens to equal the current baseline would look
  // like host scope on the next edit and be dropped. See `userOwnedFilters`.
  const ownedFields = useRef<Set<string>>(new Set());
  const savedFilters = useSavedFilters(state, stateKey, (saved) => {
    for (const field of Object.keys(saved)) ownedFields.current.add(field);
    // A saved trial type came from THIS patient's storage, so it is this
    // patient's choice. Without claiming it the staleness rule — which exists
    // to expire a type picked for someone else — would mask the very type
    // just loaded, and a later edit would overwrite it.
    if (saved.trialType !== undefined) setTrialTypeOwner(patientIdentity);
    setFilters((current) => ({ ...current, ...saved }));
  });
  // Ownership is per patient: what the previous one had saved is not evidence
  // about this one. Kept in step with `stateKey` — the same key the saved-set
  // load is keyed on, so the clear lands before that patient's answer does.
  // (The reader's session FILTERS are deliberately kept across the switch; it
  // is the claim about who owns them that does not carry over.)
  useEffect(() => {
    ownedFields.current = new Set();
  }, [stateKey]);

  // NOTE on switching patients in place: the reader's session filters are
  // deliberately KEPT, and only the trial-type ownership is re-decided (see
  // `setTrialTypeOwner` below and "a trial type belongs to the patient it was
  // chosen for"). Review flagged the saved-set overlay as leaking filters from
  // one patient to the next; resetting to the seed instead breaks that tested
  // decision. The filters belong to the reader's search, not to the patient —
  // what belongs to the patient is the SAVED set, and the overlay applies the
  // new patient's own saved keys over the top.

  const setFavorite = useSetTrialState(state, "favorites", stateKey);
  const setRegistered = useSetTrialState(state, "registered", stateKey);

  // What each trial's writes are doing, remembered here rather than read
  // off the mutation.
  //
  // One `useMutation` stands in for a per-trial operation, and it answers
  // only for the LAST one submitted. Both of its flags were wrong for the
  // same reason:
  //
  //   - `isError` answers "did the last write fail", not "did the write for
  //     THIS trial fail". Filtering by `variables.trialId` addresses the
  //     message to the right trial but does not make it durable: a rejection
  //     that lands after the reader has moved on has no trial on screen to
  //     belong to, and the next click on any trial discards it. Both cases
  //     end with a patient who was shown "Saving…" and never told otherwise.
  //
  //   - `isPending` with the same filter goes FALSE for trial A as soon as a
  //     write for trial B is submitted, because `variables` is B's. Reopen A
  //     and its button is live again, with A's PATCH still on the wire —
  //     the two-writes-in-flight race the guard exists to prevent.
  //
  // And it belongs to a PATIENT as much as to a trial. A host can swap
  // `personId` or the inline payload at any moment, including while a write
  // is on the wire; unkeyed, the previous patient's failure became this
  // one's error message, and a trial id both have in common stayed busy.
  //
  // Two mechanisms, and they are not the same one twice: the record is
  // DROPPED when the patient changes, and a callback that arrives after the
  // change finds a key that no longer matches and writes nothing. Neither
  // covers the other's case.
  const [writes, setWrites] = useState<
    { key: string } & Record<StateKind, WriteState>
  >(() => ({
    key: stateKey,
    favorites: { pending: [], failed: [] },
    registered: { pending: [], failed: [] },
  }));
  // Adjusted during render, like the tab fallback above, so the messages on
  // screen belong to the patient on screen. An effect would paint one frame
  // of the previous patient's failures first.
  if (writes.key !== stateKey) {
    setWrites({
      key: stateKey,
      favorites: { pending: [], failed: [] },
      registered: { pending: [], failed: [] },
    });
  }
  const mark = (
    key: string,
    kind: StateKind,
    field: keyof WriteState,
    trialId: string,
    on: boolean,
  ) =>
    setWrites((prev) => {
      if (prev.key !== key) return prev;
      const list = prev[kind][field];
      if (list.includes(trialId) === on) return prev;
      return {
        ...prev,
        [kind]: {
          ...prev[kind],
          [field]: on ? [...list, trialId] : list.filter((id) => id !== trialId),
        },
      };
    });
  const write = (
    kind: StateKind,
    mutation: typeof setFavorite,
    trialId: string,
    on: boolean,
  ) => {
    if (writes[kind].pending.includes(trialId)) return;
    const key = stateKey;
    mark(key, kind, "pending", trialId, true);
    mutation.mutate(
      { trialId, on },
      {
        onError: () => mark(key, kind, "failed", trialId, true),
        onSuccess: () => mark(key, kind, "failed", trialId, false),
        onSettled: () => mark(key, kind, "pending", trialId, false),
      },
    );
  };

  const stateCounts = {
    favorites: favorites.data?.length,
    registered: registered.data?.length,
  };

  // Which ids narrow the list, if the active tab is one of the state tabs.
  //
  // `undefined` while the ids are still loading — NOT `[]`, which the server
  // reads as "none of them" and would answer with an empty list a moment
  // before the real one arrives. The query waits instead.
  const stateTab = activeTabDef.needsState;
  const stateIdsQuery = stateTab === "registered" ? registered : favorites;
  const savedIds = stateTab ? (stateIdsQuery.data as string[] | undefined) : undefined;
  // The server refuses a list past its cap, so sending one means the tab
  // simply never loads while its badge cheerfully reports the count. Say
  // what happened instead — the plan called for an explicit degradation
  // here and this is it.
  const tooManySavedIds = savedIds != null && savedIds.length > MAX_TRIAL_IDS;
  const trialIds = tooManySavedIds ? undefined : savedIds;
  // `isPending`, not `data === undefined`: a rejected fetch also has no
  // data, and treating that as "still loading" left the tab on
  // "Loading trials…" for ever, with the trials query disabled so even its
  // error branch could never speak.
  const waitingForIds = stateTab != null && stateIdsQuery.isPending;
  // `data === undefined` too: a failed REFETCH keeps the ids it had, and
  // narrowing by slightly stale bookmarks beats blanking the tab and saying
  // it could not be loaded when it could.
  const idsFailed =
    stateTab != null && stateIdsQuery.isError && stateIdsQuery.data === undefined;
  // Mutually exclusive by construction: a query that has rejected is no
  // longer pending. Guarding the loading line with `!idsFailed` as well
  // would be a second mechanism for the same thing, and would make the
  // first untestable — which is how it was written the first time.
  const queryFilters = useMemo(
    () => ({
      ...effectiveFilters,
      searchTitle: debouncedTitle,
      searchTreatment: debouncedTreatment,
      sponsor: debouncedSponsor,
      distance: debouncedDistance,
      distanceUnits: debouncedDistanceUnits,
      type: activeTabDef.param,
      sort: sort as FilterState["sort"],
    }),
    [
      effectiveFilters,
      debouncedTitle,
      debouncedTreatment,
      debouncedSponsor,
      debouncedDistance,
      debouncedDistanceUnits,
      activeTabDef.param,
      sort,
    ],
  );

  const query = useTrials({
    apiClient,
    patientInfo,
    personId,
    filters: queryFilters,
    page,
    limit: PAGE_SIZE,
    trialIds,
    // `!idsFailed` too: without it a failed Favorites read still fires an
    // ordinary unfiltered search behind the error message — rows nobody
    // shows, and a full matcher run to produce them.
    enabled: !waitingForIds && !tooManySavedIds && !idsFailed,
  });

  // While the ids are loading, the rows on screen belong to the previous
  // tab. React Query is serving them from its cache — `trialIds` is still
  // `undefined`, which is the *default* tab's query key — so this is not a
  // request that can be prevented, it is a render that must not happen:
  // showing the eligible list under the Favorites heading says those trials
  // are bookmarked.
  // On a state tab, rows are shown only when they are THIS tab's rows.
  //
  // `waitingForIds` alone closed just the first half of the window: once
  // the ids arrive it goes false in the same render that changes the query
  // key, and `keepPreviousData` then hands back the previous key's rows —
  // so the eligible list was painted under the Favorites heading, which is
  // precisely the claim it was written to prevent. Worse on a second visit,
  // where the ids are already cached and the flag is false from the start.
  // `isPlaceholderData` alone, and only while the query can still resolve.
  //
  // Two corrections live in this line. An earlier version also tested
  // `isFetching`, added while the leak test was failing for an unrelated
  // reason (the test's fake server ignored the id filter, so the right and
  // wrong rows were identical). Once that was fixed the clause turned out
  // to be unnecessary — nothing could be made to leak without it — and it
  // is not free: it blanked the list on every background refetch, which
  // includes the window-focus one real hosts have on and the one that
  // follows every bookmark.
  //
  // And the two states that DISABLE the query are excluded, because a
  // disabled query still resolves `keepPreviousData`: with no cached data
  // under the fallback key, `isPlaceholderData` stays true for ever and
  // nothing will ever fetch it, so "Loading trials…" sat next to the error
  // message permanently.
  const showingOtherTabsRows =
    stateTab != null && !idsFailed && !tooManySavedIds && query.isPlaceholderData;
  const trials =
    waitingForIds || idsFailed || tooManySavedIds || showingOtherTabsRows
      ? []
      : query.data?.results ?? [];
  const totalCount = query.data?.itemsTotalCount ?? null;
  const tabCounts = query.data?.tabCounts;
  // The server's own page total (`count`), not `ceil(items / PAGE_SIZE)`:
  // the two agree only while the client's page size matches what the server
  // actually applied, and the server is the one that decides.
  const pageCount = query.data?.count ?? 0;

  // Reset to the first page whenever the *effective* query changes — tab,
  // sort, or a filter that has finished debouncing. Adjusting state during
  // render rather than in an effect (the pattern React documents for derived
  // state) so the reset is part of the same render that changes the filter:
  // an effect would let one request go out for page N of the new filter
  // first, and a request for a page past the new end is a 404 from DRF's
  // paginator, not an empty list.
  //
  // Keyed on the debounced filters for the same reason: resetting the page
  // the instant a key is pressed would fire a request for page 1 of the
  // *previous* filter, which the user sees as a flash of unfiltered results.
  // The tab and the ids belong in this signature, not just the filters.
  // `queryFilters.type` is `undefined` for the default tab AND for both
  // state tabs — `JSON.stringify` drops undefined keys, so all three
  // hashed identically and switching between them never reset the page.
  // A reader on page 2 then asked for page 2 of their bookmarks.
  const queryKey = JSON.stringify([queryFilters, activeTab, trialIds ?? null]);
  const [lastQueryKey, setLastQueryKey] = useState(queryKey);
  if (lastQueryKey !== queryKey) {
    setLastQueryKey(queryKey);
    setPage(1);
  }

  // A page past the end is a 404 (`NotFound` from PageNumberPagination), and
  // `keepPreviousData` leaves the stale page — and its stale pager — on
  // screen, so every further click reproduces it. Recover to a page that
  // exists. Reachable when the host restores a `?page=` from its own URL, or
  // when a click lands during the window where the pager is still showing
  // the previous response's page count.
  const isPageNotFound =
    query.isError &&
    (query.error as { response?: { status?: number } })?.response?.status === 404;
  useEffect(() => {
    if (isPageNotFound && page !== 1) setPage(1);
  }, [isPageNotFound, page]);

  const handleTabChange = (next: TabValue) => setActiveTab(next);
  const handleSortChange = (next: string) => setSort(next);
  const handleFiltersChange = (next: FilterState) => {
    // The reader picking a type claims it for the patient on screen. The
    // panel is fed `effectiveFilters`, so an unrelated edit hands back the
    // masked value unchanged and this does not fire.
    if (next.trialType !== effectiveFilters.trialType) {
      setTrialTypeOwner(patientIdentity);
    }
    setFilters(next);
    // Only what the reader changed. `next` carries the host's mount-time
    // scope too, and saving that would make one mount's scope their standing
    // preference — a later mount with a different scope would lose to it.
    const owned = userOwnedFilters(next, baseline, ownedFields.current);
    for (const field of Object.keys(owned)) ownedFields.current.add(field);
    savedFilters.persist(owned);
  };
  // Reset goes back to the baseline, not to `{}`: clearing the seeded
  // country would silently widen the search to every country in the
  // registry, which is not what "reset" means to the person clicking it.
  // The owner is deliberately NOT cleared here. Once a stale choice falls
  // back to the baseline's type, clearing it changes nothing — the two
  // produce the same value — and a mutation test confirmed the line was
  // dead. It was load-bearing only under the earlier "mask to undefined"
  // rule, which is gone.
  const handleFiltersReset = () => {
    setFilters(baseline);
    // Reset gives the fields back to the host, so nothing is owned any more.
    ownedFields.current = new Set();
    // `reset`, not `persist(baseline)`: the server merges a partial update, so
    // writing the baseline would leave whatever the reader had saved for keys
    // the baseline does not mention. It also retires a save already on the
    // wire, which would otherwise land afterwards and restore what was
    // just cleared.
    savedFilters.reset();
  };

  const handleSelect = (trial: TrialMatch) => {
    setSelectedTrial(trial);
    onTrialSelect?.(trial);
  };

  // When the detail view opens, push a synthetic history entry so the
  // browser ← back button returns to the trial list instead of navigating
  // to the previous host page. The popstate listener tears itself down
  // when the detail closes (effect cleanup) or when the patient context
  // resets (selectedTrial becomes null via the reset effect above).
  useEffect(() => {
    if (!selectedTrial) return;
    window.history.pushState({ exactTrialDetail: selectedTrial.trialId }, "");
    const handler = () => setSelectedTrial(null);
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, [selectedTrial]);

  // Selecting a trial swaps the whole view for the in-remote detail page
  // (CB navigates to its own `/t/:id`; the remote owns the detail itself).
  // `onBack` calls history.back() so the synthetic entry is consumed and
  // the popstate listener above fires setSelectedTrial(null).
  if (selectedTrial) {
    const selectedId = String(selectedTrial.trialId);
    return (
      <TrialDetailPage
        apiClient={apiClient}
        trialId={selectedTrial.trialId}
        patientInfo={patientInfo}
        personId={personId}
        // Values and callbacks, not the adapter: this component owns the id
        // lists and the mutations, so it is the only place that can keep the
        // star on the card and the star on the detail page saying the same
        // thing. `undefined` where the answer is not known — the control is
        // then not drawn at all, rather than drawn wrong for a moment.
        trialState={
          state
            ? {
                isFavorite: favorites.data
                  ? favorites.data.includes(selectedId)
                  : undefined,
                favoriteBusy: writes.favorites.pending.includes(selectedId),
                onToggleFavorite: (on) =>
                  write("favorites", setFavorite, selectedId, on),
                favoriteFailed: writes.favorites.failed.includes(selectedId),
                // `data === undefined` too: a query that has errored KEEPS
                // the data it had, so after a failed background refetch this
                // was true while the star was still being drawn from the
                // retained ids — an "unavailable" notice printed underneath
                // a control that is present and works.
                favoritesUnavailable: favorites.isError && favorites.data === undefined,
                isRegistered:
                  registered.data && advanced.data
                    ? registered.data.includes(selectedId)
                    : undefined,
                onToggleRegistered: (on) =>
                  write("registered", setRegistered, selectedId, on),
                // Pending IS read off the mutation, and is scoped the same
                // way: it describes a write in flight, which there can only
                // be one of, and it must not disable a different trial's
                // button.
                registerPending: writes.registered.pending.includes(selectedId),
                registerFailed: writes.registered.failed.includes(selectedId),
                registeredUnavailable:
                  (registered.isError && registered.data === undefined) ||
                  (advanced.isError && advanced.data === undefined) ||
                  !canReadAdvanced(state),
                // Absent until the read answers: drawn against an unknown
                // answer, the control is exactly the one that overwrites an
                // advanced status.
                advancedStatus: advanced.data?.[selectedId],
              }
            : undefined
        }
        // The derived set, not raw state: the detail is scored under the
        // preferences the list used, and `country` no longer lives in
        // `filters`. Passing raw state sent the detail request without the
        // patient's country, so its scores and distance could disagree with
        // the card the reader clicked.
        filters={effectiveFilters}
        onBack={() => window.history.back()}
      />
    );
  }

  return (
    <div className="exact-root exact-list" style={{ padding: "1rem" }}>
      <h1 className="exact-list__title">Your Trials</h1>

      <Tabs
        tabs={tabs}
        active={activeTab}
        onChange={handleTabChange}
        // Withheld while a state tab is active: those counts came back from
        // a request narrowed to the saved ids, so they describe the
        // bookmarks, not the corpus. Painted on the Eligible / Fully
        // matched / Potential badges they would read as the corpus —
        // "Fully matched, 1" for a reader who has one bookmarked eligible
        // trial and two hundred matching ones.
        counts={stateTab ? undefined : tabCounts}
        activeTabTotal={stateTab ? null : totalCount}
        stateCounts={stateCounts}
      />

      <div className="exact-list__controls">
        <SortControl value={sort} onChange={handleSortChange} />

        <button
          type="button"
          className={`exact-filters__trigger${
            filtersOpen || activeFilterCount > 0 ? " is-on" : ""
          }`}
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen((open) => !open)}
        >
          {activeFilterCount > 0
            ? `Filters (${activeFilterCount})`
            : "Filter Results"}
        </button>
      </div>

      {filtersOpen ? (
        <FilterPanel
          apiClient={apiClient}
          filters={effectiveFilters}
          onChange={handleFiltersChange}
          onReset={handleFiltersReset}
          canReset={activeFilterCount > 0}
          diseaseCode={diseaseCode}
        />
      ) : null}

      {query.isLoading || waitingForIds || showingOtherTabsRows ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>Loading trials…</p>
      ) : null}

      {tooManySavedIds ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          You have saved {savedIds?.length} trials, and this view can show at
          most {MAX_TRIAL_IDS} at a time. Remove a few, or use the other tabs
          to find them.
        </p>
      ) : null}

      {idsFailed ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          Couldn't load your saved trials:{" "}
          {(stateIdsQuery.error as Error)?.message ?? "unknown error"}
        </p>
      ) : null}

      {/* The bookmark control is painted from the favorites list, so when
          that read fails every star vanishes — on EVERY tab, not just the
          Favorites one, and with nothing on screen to say why. The reader
          would conclude the feature had been removed. */}
      {favorites.isError && favorites.data === undefined && !idsFailed ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          Couldn't load your favorites, so bookmarking is unavailable right
          now.
        </p>
      ) : null}

      {/* A write that failed has to say so. The star is painted from the
          server's list, so a rejected PATCH leaves it exactly where it was —
          indistinguishable from a click that never registered.

          From the recorded failures, not from the mutation: a registration
          that rejects after the reader has gone back to the list had no
          surface here at all, so the only thing they ever saw was
          "Saving…". */}
      {writes.favorites.failed.length ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }} role="alert">
          Couldn't update your favorites. Please try again.
        </p>
      ) : null}

      {writes.registered.failed.length ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }} role="alert">
          Couldn't save your interest in {writes.registered.failed.length === 1
            ? "a trial"
            : `${writes.registered.failed.length} trials`}
          . Open the trial to try again.
        </p>
      ) : null}

      {query.isError ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          Failed to load trials: {(query.error as Error)?.message ?? "unknown error"}
        </p>
      ) : null}

      {/* `isPlaceholderData`, not `isFetching`: the rows on screen belong to
          the previous query only while placeholder data is showing. Keyed on
          `isFetching` this dimmed the whole list on every background refetch
          — including the window-focus one React Query runs by default after
          30s away — for a request the reader never asked for.

          The live region is always mounted and swaps its text: several
          screen readers only announce changes to a region that already
          existed, so a conditionally-rendered `role="status"` is silent. */}
      <div className="exact-list__updating-slot" role="status" aria-live="polite">
        {query.isPlaceholderData ? (
          <span className="exact-list__updating">Updating…</span>
        ) : null}
      </div>

      <div
        className={`exact-list__rows${query.isPlaceholderData ? " is-stale" : ""}`}
        aria-busy={query.isPlaceholderData || undefined}
      >
        {trials.map((t) => (
          <TrialCard
            key={t.trialId}
            trial={t}
            onSelect={handleSelect}
            isFavorite={
              favorites.data ? favorites.data.includes(String(t.trialId)) : undefined
            }
            busy={writes.favorites.pending.includes(String(t.trialId))}
            onToggleFavorite={
              state
                ? (on) => write("favorites", setFavorite, String(t.trialId), on)
                : undefined
            }
          />
        ))}
      </div>

      {!query.isLoading && patientInfo == null && personId == null ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>
          Pass a <code>patientInfo</code> payload or <code>personId</code> to load
          trial matches.
        </p>
      ) : null}

      {!query.isLoading &&
      !waitingForIds &&
      !idsFailed &&
      !tooManySavedIds &&
      !showingOtherTabsRows &&
      (patientInfo != null || personId != null) &&
      trials.length === 0 ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>No trials found</p>
      ) : null}

      <Pagination
        page={page}
        pageCount={pageCount}
        onChange={setPage}
      />
    </div>
  );
}

export function TrialMatches(props: TrialMatchesProps) {
  // If the host provides a QueryClient we use it; otherwise spin up our
  // own. Keeping the local one stable across renders avoids React Query's
  // re-mount thrash when the parent re-renders for unrelated reasons.
  const [ownClient] = useState(() => new QueryClient());
  const client = props.queryClient ?? ownClient;

  return (
    <QueryClientProvider client={client}>
      <TrialMatchesInner {...props} />
    </QueryClientProvider>
  );
}

export default TrialMatches;
