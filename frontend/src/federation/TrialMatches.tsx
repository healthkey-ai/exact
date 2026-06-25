// Federated `./TrialMatches` export (#104, part of #101). Renders the
// patient's trial matches grouped by `matchingType`, with a filter bar,
// inline detail view, and host-agnostic axios injection. The host
// supplies either `patientInfo` (inline payload — matches the existing
// CB contract) or `personId` (CTOMOP federation path added in #102).
import { useEffect, useMemo, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FilterBar } from "./FilterBar";
import { TrialCard } from "./TrialCard";
import { TrialDetailPage } from "./TrialDetailPage";
import { groupByMatchingType } from "./grouping";
import { useTrials } from "./hooks";
import { injectStyles } from "./injectStyles";
import type { FilterState, MatchingType, TrialMatch, TrialMatchesProps } from "./types";

// `not_eligible` is included for forward-compat with a future call that
// surfaces ineligible matches (e.g. an opt-in "show why these don't fit"
// path). Today the server-side `TrialSerializer.to_representation`
// only ever emits `'eligible' | 'potential'`, so the bucket is
// rendered only when non-empty — empty buckets are hidden below.
const GROUP_ORDER: MatchingType[] = ["eligible", "potential", "not_eligible"];

const GROUP_LABELS: Record<MatchingType, string> = {
  eligible: "Eligible",
  potential: "Potential",
  not_eligible: "Not eligible",
};

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
  }, [personId, patientInfoKey]);

  // Auto-derive `country` from the patient profile. The country filter
  // was previously a dropdown but in practice was redundant — patients
  // are matched to trials in their home country. We sync on every
  // patient change (not just first mount) so swapping `patientInfo`
  // — e.g. picking another row in the dev harness — re-scopes the
  // trial list correctly. The equality guard prevents a `setFilters`
  // re-render storm when the patient's country is already the active
  // filter. `undefined` clears the param so a patient without a country
  // gets the disease-agnostic union, not a stale previous country.
  const patientCountry = useMemo(() => {
    const c = (patientInfo as Record<string, unknown> | null | undefined)?.["country"];
    return typeof c === "string" && c.trim() ? c.trim() : undefined;
  }, [patientInfo]);
  useEffect(() => {
    if (filters.country === patientCountry) return;
    setFilters((prev) => ({ ...prev, country: patientCountry }));
  }, [patientCountry, filters.country]);

  const query = useTrials({ apiClient, patientInfo, personId, filters });

  const allTrials = useMemo(
    () => query.data?.pages.flatMap((p) => p.results) ?? [],
    [query.data],
  );

  const totalCount = query.data?.pages[0]?.itemsTotalCount ?? null;

  const grouped = useMemo(
    () => groupByMatchingType(allTrials),
    [allTrials],
  );

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
    <div className="exact-root" style={{ padding: "1rem" }}>
      <FilterBar
        apiClient={apiClient}
        filters={filters}
        onChange={setFilters}
        diseaseCode={diseaseCode}
      />

      {query.isLoading ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>Loading trials…</p>
      ) : null}

      {query.isError ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          Failed to load trials: {(query.error as Error)?.message ?? "unknown error"}
        </p>
      ) : null}

      {GROUP_ORDER.map((group) => {
        const trials = grouped[group];
        if (!trials.length) return null;
        return (
          <section key={group} style={{ marginBottom: "1.5rem" }}>
            <h3
              style={{
                fontSize: "0.875rem",
                fontWeight: 600,
                color: "var(--exact-color-text-muted)",
                margin: "0 0 0.5rem",
              }}
            >
              {GROUP_LABELS[group]} ({trials.length})
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              {trials.map((t) => (
                <TrialCard key={t.trialId} trial={t} onSelect={handleSelect} />
              ))}
            </div>
          </section>
        );
      })}

      {!query.isLoading && patientInfo == null && personId == null ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>
          Pass a <code>patientInfo</code> payload or <code>personId</code> to load
          trial matches.
        </p>
      ) : null}

      {!query.isLoading &&
      (patientInfo != null || personId != null) &&
      allTrials.length === 0 ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>
          No trials matched the current filters.
        </p>
      ) : null}

      {query.hasNextPage ? (
        <div style={{ textAlign: "center", marginTop: "1rem" }}>
          <button
            onClick={() => query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
            style={{
              padding: "0.5rem 1.25rem",
              borderRadius: "0.375rem",
              border: "1px solid var(--exact-color-border, #d1d5db)",
              background: "var(--exact-color-surface, #fff)",
              color: "var(--exact-color-text, #111827)",
              cursor: query.isFetchingNextPage ? "wait" : "pointer",
              fontSize: "0.875rem",
            }}
          >
            {query.isFetchingNextPage
              ? "Loading…"
              : `Load more (${allTrials.length} / ${totalCount ?? "…"})`}
          </button>
        </div>
      ) : null}
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
