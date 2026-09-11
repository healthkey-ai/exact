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
  const activeTabDef = TABS.find((t) => t.value === activeTab) ?? TABS[0];
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
  const handleFiltersChange = (next: FilterState) => {
    // The reader picking a type claims it for the patient on screen. The
    // panel is fed `effectiveFilters`, so an unrelated edit hands back the
    // masked value unchanged and this does not fire.
    if (next.trialType !== effectiveFilters.trialType) {
      setTrialTypeOwner(patientIdentity);
    }
    setFilters(next);
  };
  // Reset goes back to the baseline, not to `{}`: clearing the seeded
  // country would silently widen the search to every country in the
  // registry, which is not what "reset" means to the person clicking it.
  // The owner is deliberately NOT cleared here. Once a stale choice falls
  // back to the baseline's type, clearing it changes nothing — the two
  // produce the same value — and a mutation test confirmed the line was
  // dead. It was load-bearing only under the earlier "mask to undefined"
  // rule, which is gone.
  const handleFiltersReset = () => setFilters(baseline);

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
          filters={effectiveFilters}
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
