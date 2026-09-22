// Pure logic behind the list chrome — tabs, sort options, pagination.
// Kept out of the components so it can be tested without rendering (the
// suite runs in a node environment; see vitest.config.ts).

import type { TabCounts } from "./types";

/** Rows per page. CB shows 10; the server's own default is 20. */
export const PAGE_SIZE = 10;

/** CB's tab bar, as far as this remote can honestly reproduce it.
 *
 *  CB's is Eligible / All Trials (admin) / Registered / Favorites. The last
 *  two are per-user relations EXACT does not hold — the server rejects
 *  `?type=favorites` and `?type=my_trials` outright (EXACT #417) — so they
 *  are narrowed a different way: the ids come from the state adapter and go
 *  down as `trial_ids`, which EXACT applies inside the queryset (#419).
 *
 *  They are therefore CONDITIONAL on a host having supplied that adapter.
 *  Rendered without one they would be a tab that cannot answer, and a
 *  bookmark control that forgets.
 *
 *  `eligible_and_potential` is CB's default tab. It is a no-op server-side,
 *  identical to sending no `type` at all, so it maps to `undefined` here
 *  rather than to a parameter that would suggest it narrows something. */
export type TabValue =
  | "eligible_and_potential"
  | "eligible"
  | "potential"
  | "all"
  | "favorites"
  | "registered";

export interface TabDef {
  value: TabValue;
  label: string;
  /** What goes on the wire; `undefined` means "send no `type`". */
  param?: "eligible" | "potential" | "all";
  /** Narrowed by a list of ids from the state adapter rather than by
   *  `?type=`, and hidden entirely when there is no adapter. */
  needsState?: "favorites" | "registered";
}

const MATCH_TABS: TabDef[] = [
  // CB labels this one "Eligible". The label names the union instead, because
  // the count beside it is `eligible + potential` (see `tabCount`) and the
  // list holds both: the trials meeting every criterion that could be checked,
  // and the trials with information still missing.
  { value: "eligible_and_potential", label: "Eligible & Potential" },
];

/** CB's bar has no eligible-only or potential-only tab, and #517 settled that
 *  this one follows CB. They remain server values, so a host may still ask for
 *  one through `initialFilters.type` — and such a tab IS rendered while it is
 *  the active one. Without that, `TrialMatches` would find the reader on a tab
 *  the bar cannot name and reset them to the default, dropping the narrowing
 *  the host asked for. */
const DEEP_LINK_TABS: TabDef[] = [
  { value: "eligible", label: "Fully matched", param: "eligible" },
  { value: "potential", label: "Potential", param: "potential" },
];

const STATE_TABS: TabDef[] = [
  { value: "registered", label: "Registered", needsState: "registered" },
  { value: "favorites", label: "Favorites", needsState: "favorites" },
];

/** The bar to render: CB's tabs, minus the two state tabs when no adapter can
 *  answer them, plus the active deep-linked tab when a host asked for one. */
/** A deep-linked tab is a ONE-WAY door: it is in the bar only while it is
 *  active, so leaving it removes it and the reader cannot return without a
 *  remount. Deliberate — the bar is CB's, and the subset is something a host
 *  asked for rather than a view this UI offers. */
export function tabsFor(hasState: boolean, active?: TabValue): TabDef[] {
  return [
    ...MATCH_TABS,
    ...DEEP_LINK_TABS.filter((tab) => tab.value === active),
    ...(hasState ? STATE_TABS : []),
  ];
}

/** @deprecated Prefer `tabsFor`; kept as the no-adapter bar. */
export const TABS: TabDef[] = MATCH_TABS;

/** The tab whose `param` matches a `type` the host passed in `initialFilters`.
 *  Falls back to the default tab for `undefined` and for values that are not
 *  tabs (`all` is a supported server value but is not offered as a tab). */
export function tabValueForType(type: string | undefined): TabValue {
  const match = DEEP_LINK_TABS.find((tab) => tab.param === type);
  return match ? match.value : "eligible_and_potential";
}

/** The count to show next to a tab, or null when the server did not say.
 *
 *  Absence is not zero: the server omits `tabCounts` when it had no patient
 *  context, and under `?type=all`, because in both cases no per-row verdict
 *  was computed. Rendering a "0" there would state a clinical result nobody
 *  produced, so the caller shows nothing instead. */
export function tabCount(
  tab: TabValue,
  counts: TabCounts | undefined,
  itemsTotalCount: number | null,
  stateCounts?: { favorites?: number; registered?: number },
): number | null {
  // The state tabs are counted by whoever holds the state, not by the
  // matcher: their totals are how many the patient saved, which is true
  // whether or not those trials still match today.
  if (tab === "favorites") return stateCounts?.favorites ?? null;
  if (tab === "registered") return stateCounts?.registered ?? null;
  if (tab === "eligible_and_potential") {
    if (counts) return counts.eligible + counts.potential;
    // Without counts the total is only the whole corpus when this tab is
    // the active one; the caller passes null otherwise.
    return itemsTotalCount;
  }
  if (!counts) return null;
  return tab === "eligible" ? counts.eligible : counts.potential;
}

/** Every tab's count, or none at all (#536).
 *
 *  A number missing from one badge while its neighbours keep theirs does not
 *  read as "not counted" — it reads as ZERO, and it reads that way BECAUSE
 *  of the neighbours. Measured on the local stack: the bar says
 *  "Eligible & Potential 369 / Registered 1 / Favorites 1", and opening
 *  Favorites leaves "Eligible & Potential" bare beside two tabs that still
 *  have their numbers. A reader with one bookmark is being told, in the only
 *  language a badge has, that nothing matches them.
 *
 *  So a CORPUS tab that cannot be counted takes the whole bar down with it.
 *  The reasons it cannot are several — the request was narrowed to the saved
 *  ids, the saved filters or the saved ids are still on their way, the
 *  server had no patient context — and they all end the same way, which is
 *  why this asks the outcome rather than enumerating them. The cost is a
 *  Favorites count that WAS correct, since it comes from the adapter rather
 *  than from this response; a number beside a blank is what makes the blank
 *  read as zero, and it is the corpus that is blank there.
 *
 *  A STATE tab that cannot be counted takes the other state tab with it and
 *  stops there — they are two reads, and the slower one sitting blank beside
 *  the faster one's number is this same bug on a cold load. What it does not
 *  take is the corpus, and that is the asymmetry: the argument above does not
 *  run backwards. Blanking the corpus because the BOOKMARKS service is
 *  unreachable gives up the one number the page exists to show — on the view
 *  every reader lands on, and not briefly: the production client retries with
 *  backoff and does not poll, so it would stay blank until the reader did
 *  something. What it would buy is avoiding a false zero about how many
 *  trials the reader saved, which they already know and can disprove in a
 *  click. A false zero about a clinical result nobody produced is the
 *  expensive one — the same reason `tabCount` returns null rather than 0 —
 *  and the two are not the same size.
 *
 *  Either way it costs a reflow: the bar narrows when numbers go away,
 *  under a pointer that has just clicked the tab it rests on. */
export function barCounts(
  tabs: TabDef[],
  active: TabValue,
  counts: TabCounts | undefined,
  activeTabTotal: number | null,
  stateCounts?: { favorites?: number; registered?: number },
): Map<TabValue, number> | null {
  const numbered = new Map<TabValue, number>();
  const fromState: TabValue[] = [];
  let stateIncomplete = false;
  for (const tab of tabs) {
    const count = tabCount(
      tab.value,
      counts,
      // Only the active tab's own total is meaningful as a fallback;
      // labelling an inactive tab with the active tab's count would be a
      // plain lie.
      tab.value === active ? activeTabTotal : null,
      stateCounts,
    );
    // `isFinite`, not just `!= null`: `tabCounts` is unvalidated wire data
    // and the default tab's count is a SUM, so one missing field paints a
    // badge reading `NaN` and an accessible name reading "NaN trials".
    if (count == null || !Number.isFinite(count)) {
      // Written for any corpus tab, though only the default one can reach
      // it today: `tabsFor` carries a deep-link tab only while it is
      // active, and an unnumberable deep-link tab means the counts are
      // missing, which leaves the default tab unnumberable too — and it
      // comes first. So this arm is the rule, not a tested path.
      if (!tab.needsState) return null;
      stateIncomplete = true;
      continue;
    }
    if (tab.needsState) fromState.push(tab.value);
    numbered.set(tab.value, count);
  }
  // The state tabs answer together or not at all. They are two reads, not
  // one, so on an ordinary cold load the slower one is blank beside the
  // faster one's number — the same false zero this exists to prevent,
  // arriving without anyone having done anything.
  if (stateIncomplete) for (const tab of fromState) numbered.delete(tab);
  return numbered;
}

export interface SortOption {
  value: string;
  label: string;
}

/** CB's three, in CB's order. The server accepts more (`status`, `phase`,
 *  `updated`, `enrollment`, `patientBurdenScore`); those are not offered
 *  because CB does not offer them and parity is the point. */
export const SORT_OPTIONS: SortOption[] = [
  { value: "goodnessScore", label: "Sort By Suitability Score" },
  { value: "matchScore", label: "Sort by Matching Score" },
  { value: "distance", label: "Sort by Distance" },
];

export const DEFAULT_SORT = "goodnessScore";

/** How wide the controls row has to be, in CSS pixels, before the sort fits
 *  beside the view mode and the three actions.
 *
 *  A function of how many orders are offered, not a constant: `sortOptionsFor`
 *  adds a fourth segment for a host-supplied order the server takes and CB
 *  does not offer, and a threshold measured on three sends four into a row
 *  that cannot hold them. Measured in the browser: 99 (view mode) + 364
 *  (actions) + two 12px gaps, and 173 per segment; plus 50 so the segments
 *  are not pressed against the buttons. Three gives 1056. */
export function wideControlsRow(sortOptionCount: number): number {
  return 537 + 173 * sortOptionCount;
}

/** The options to render, given the value the control is actually set to.
 *
 *  The server accepts more sort keys than CB offers (`status`, `phase`,
 *  `updated`, `enrollment`, `patientBurdenScore`). A host can pass one via
 *  `initialFilters.sort`, and a `<select>` whose value matches no `<option>`
 *  renders blank while the list is genuinely sorted that way — the control
 *  would be lying about what it is doing. Surface the value instead. */
export function sortOptionsFor(value: string): SortOption[] {
  if (SORT_OPTIONS.some((option) => option.value === value)) return SORT_OPTIONS;
  return [...SORT_OPTIONS, { value, label: `Sorted by ${value}` }];
}

/** Page numbers to render, with "…" where the run is broken.
 *
 *  Ported from CB `pages/Trials.tsx` so the two paginations look and behave
 *  the same: every page up to 7, otherwise first, last, the current page
 *  and its neighbours, with ellipses filling the gaps. */
export function getPageNumbers(
  currentPage: number,
  total: number,
): (number | "…")[] {
  const pages: (number | "…")[] = [];
  if (total <= 0) return pages;

  if (total <= 7) {
    for (let i = 1; i <= total; i++) pages.push(i);
    return pages;
  }

  pages.push(1);
  if (currentPage > 3) pages.push("…");

  for (
    let i = Math.max(2, currentPage - 1);
    i <= Math.min(total - 1, currentPage + 1);
    i++
  ) {
    pages.push(i);
  }

  if (currentPage < total - 2) pages.push("…");
  pages.push(total);
  return pages;
}
