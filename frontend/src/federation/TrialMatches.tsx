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
import { baselineFilters, countActiveFilters, countryFor } from "./filters";
import {
  DEFAULT_SORT,
  PAGE_SIZE,
  TABS,
  tabValueForType,
  type TabValue,
} from "./listChrome";
import { useTrials } from "./hooks";
import { injectStyles } from "./injectStyles";
import type { FilterState, TrialMatch, TrialMatchesProps } from "./types";

function TrialMatchesInner({
  apiClient,
  patientInfo,
  personId,
  initialFilters,
  onTrialSelect,
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

  // `country` is seeded from the patient profile — patients are matched to
  // trials they can reach — but the panel now offers the control, so the
  // seeding happens ONCE PER PATIENT rather than continuously. The previous
  // effect re-asserted the patient's country whenever it differed from the
  // filter, which with a control on screen would have snapped the user's
  // own choice back on the very next render.
  const patientCountry = useMemo(() => {
    const c = (patientInfo as Record<string, unknown> | null | undefined)?.["country"];
    return typeof c === "string" && c.trim() ? c.trim() : undefined;
  }, [patientInfo]);
  // Keyed on WHICH PATIENT, not on the country value. Keyed on the value,
  // a reader who overrode Patient A's country and then had the host swap to
  // Patient B in the same country would keep searching A's override: the
  // marker never changed, so the new patient was never seeded.
  //
  // `hasInlinePatient` decides which prop identifies the patient, because it
  // is the same function the request uses to decide which one it sends. When
  // this disagreed with that, a host updating the inline payload while
  // keeping a person id carried the previous patient's filters into the new
  // patient's search.
  const patientIdentity = hasInlinePatient(patientInfo)
    ? patientInfoKey
    : personId != null
      ? String(personId)
      : null;
  const UNSEEDED = "\u0000unseeded";
  const seededFor = useRef<string | null>(UNSEEDED);
  if (seededFor.current !== patientIdentity) {
    // During render, not in an effect: an effect would let one request go
    // out with the previous patient's country. `useRef` rather than state
    // because this is a "have I done this yet" marker, not rendered data.
    const isFirstSeed = seededFor.current === UNSEEDED;
    seededFor.current = patientIdentity;
    setFilters((prev) => ({
      ...prev,
      // The same answer the baseline computes. Written separately, the two
      // disagreed whenever the patient had no country and the host had
      // supplied one: this cleared it, the baseline kept it, and the badge
      // read "Filters (1)" for a country that was never sent.
      country: countryFor(patientCountry, initialFilters),
      // Cleared when SWITCHING patients, not on the first seed: the options
      // are disease-scoped, so a type picked for an MM patient is invisible
      // in a BC patient's list and `by_trial_type` has no leniency for a
      // value that is not there — the reader would get an empty result set
      // from a control rendering blank. CB carries the structurally
      // identical guard for its purpose->type narrowing. On mount there is
      // no previous patient, and clearing would throw away a `trialType` the
      // host asked for in `initialFilters`.
      ...(isFirstSeed ? {} : { trialType: undefined }),
    }));
  }

  const baseline = useMemo(
    () => baselineFilters(patientCountry, initialFilters),
    [patientCountry, initialFilters],
  );
  const activeFilterCount = countActiveFilters(filters, baseline);

  const debouncedTitle = useDebounced(filters.searchTitle, 400);
  const debouncedTreatment = useDebounced(filters.searchTreatment, 400);
  const debouncedSponsor = useDebounced(filters.sponsor, 400);
  const debouncedDistance = useDebounced(filters.distance, 400);
  const debouncedDistanceUnits = useDebounced(filters.distanceUnits, 400);
  const activeTabDef = TABS.find((t) => t.value === activeTab) ?? TABS[0];
  const queryFilters = useMemo(
    () => ({
      ...filters,
      searchTitle: debouncedTitle,
      searchTreatment: debouncedTreatment,
      sponsor: debouncedSponsor,
      distance: debouncedDistance,
      distanceUnits: debouncedDistanceUnits,
      type: activeTabDef.param,
      sort: sort as FilterState["sort"],
    }),
    [
      filters,
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
  });

  const trials = query.data?.results ?? [];
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
  const queryKey = JSON.stringify(queryFilters);
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
  const handleFiltersChange = (next: FilterState) => setFilters(next);
  // Reset goes back to the baseline, not to `{}`: clearing the seeded
  // country would silently widen the search to every country in the
  // registry, which is not what "reset" means to the person clicking it.
  const handleFiltersReset = () => setFilters(baseline);

  const diseaseCode = useMemo(() => {
    const d = (patientInfo as Record<string, unknown> | null | undefined)?.["disease"];
    return typeof d === "string" ? d : undefined;
  }, [patientInfo]);

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
    return (
      <TrialDetailPage
        apiClient={apiClient}
        trialId={selectedTrial.trialId}
        patientInfo={patientInfo}
        personId={personId}
        filters={filters}
        onBack={() => window.history.back()}
      />
    );
  }

  return (
    <div className="exact-root exact-list" style={{ padding: "1rem" }}>
      <h1 className="exact-list__title">Your Trials</h1>

      <Tabs
        active={activeTab}
        onChange={handleTabChange}
        counts={tabCounts}
        activeTabTotal={totalCount}
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
          filters={filters}
          onChange={handleFiltersChange}
          onReset={handleFiltersReset}
          canReset={activeFilterCount > 0}
          diseaseCode={diseaseCode}
        />
      ) : null}

      {query.isLoading ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>Loading trials…</p>
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
          <TrialCard key={t.trialId} trial={t} onSelect={handleSelect} />
        ))}
      </div>

      {!query.isLoading && patientInfo == null && personId == null ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>
          Pass a <code>patientInfo</code> payload or <code>personId</code> to load
          trial matches.
        </p>
      ) : null}

      {!query.isLoading &&
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
