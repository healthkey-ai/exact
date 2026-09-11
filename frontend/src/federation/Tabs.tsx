// Tab bar over the trial list, mirroring CB's `TabButton` row on Your
// Trials. Structure lives in `exact.css` (`.exact-tabs*`).
import { tabCount, type TabDef, type TabValue } from "./listChrome";
import type { TabCounts } from "./types";

interface Props {
  /** The bar to render — which tabs exist depends on whether the host gave
   *  the component somewhere to keep bookmarks. */
  tabs: TabDef[];
  active: TabValue;
  onChange: (tab: TabValue) => void;
  /** Server-side totals over the whole matched corpus. Absent when the
   *  server could not judge — see `tabCount`. */
  counts?: TabCounts;
  /** `itemsTotalCount` of the current response, used only to label the
   *  default tab when the server sent no counts and that tab is the one
   *  being listed. */
  activeTabTotal: number | null;
  /** Totals for the state-backed tabs, counted by whoever holds the state —
   *  how many the patient saved, which is true whether or not those trials
   *  still match today. */
  stateCounts?: { favorites?: number; registered?: number };
}

export function Tabs({
  tabs,
  active,
  onChange,
  counts,
  activeTabTotal,
  stateCounts,
}: Props) {
  // Plain buttons in a <nav>, not role="tablist"/"tab". The ARIA tab pattern
  // promises arrow-key navigation, a roving tabindex, and an associated
  // tabpanel; announcing "tab, 1 of 3" and then not moving on ← / → is worse
  // for a screen-reader user than not claiming the pattern at all. These are
  // filters over one list, and `aria-current` says which one is applied.
  return (
    <nav className="exact-tabs" aria-label="Filter trials by match status">
      {tabs.map((tab) => {
        const isActive = tab.value === active;
        const count = tabCount(
          tab.value,
          counts,
          // Only the active tab's own total is meaningful as a fallback;
          // labelling an inactive tab with the active tab's count would be
          // a plain lie.
          isActive ? activeTabTotal : null,
          stateCounts,
        );
        return (
          <button
            key={tab.value}
            type="button"
            aria-current={isActive || undefined}
            // The label and the count are separate elements with no
            // whitespace between them, so the accessible name computes to
            // "Eligible19". Spelled out here instead.
            aria-label={count != null ? `${tab.label}, ${count} trials` : tab.label}
            className={`exact-tab${isActive ? " is-active" : ""}`}
            onClick={() => onChange(tab.value)}
          >
            <span className="exact-tab__label">{tab.label}</span>
            {count != null ? (
              // `data-testid` because the button's accessible name is an
              // `aria-label` computed above: a test that reads only the name
              // cannot see what is actually painted here, and would pass
              // against a badge rendering `0`.
              <span className="exact-tab__count" data-testid="tab-count">
                {count}
              </span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
}
