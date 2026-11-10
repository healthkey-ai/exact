// Tab bar over the trial list, mirroring CB's `TabButton` row on Your
// Trials. Structure lives in `exact.css` (`.exact-tabs*`).
import { useCallback, useLayoutEffect, useRef } from "react";

import { ActionTooltip } from "./bits";
import { barCounts, type TabDef, type TabValue } from "./listChrome";
import { TAB_TOOLTIPS } from "./tooltips";
import type { TabCounts } from "./types";

interface Props {
  /** The bar to render — which tabs exist depends on whether the host gave
   *  the component somewhere to keep bookmarks. */
  tabs: TabDef[];
  active: TabValue;
  onChange: (tab: TabValue) => void;
  /** Server-side totals over the whole matched corpus. Absent when the
   *  server could not judge, and withheld by the caller when the request was
   *  narrowed to the saved ids — see `barCounts`. */
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
  // The strip scrolls now that it no longer widens the page, so the tab in
  // force can start off the right edge — a reader whose saved tab is
  // Favorites would see a row that looks truncated and no sign that the one
  // they are on is out there. Browsers scroll a FOCUSED control into view on
  // their own; nothing does it for a selected one.
  const stripRef = useRef<HTMLElement | null>(null);
  const activeRef = useRef<HTMLButtonElement>(null);
  const observerRef = useRef<ResizeObserver | null>(null);
  // Set when the READER moves the strip, which is the one case none of this
  // may undo. Told apart from our own scrolling by the position: we know
  // what we set, and a scroll event that arrives at exactly that position is
  // the one we caused.
  const readerScrolled = useRef(false);
  const ourScroll = useRef<number | null>(null);
  // How wide the tabs were last time we looked, and whether the last thing
  // that happened to the strip was them getting narrower. See `noteScroll`.
  const contentWidth = useRef(0);
  const lastScroll = useRef(0);
  const absorbClamp = useRef(false);
  const keepActiveInView = useCallback(() => {
    const strip = stripRef.current;
    const tab = activeRef.current;
    if (!strip || !tab) return;
    // The strip's own `scrollLeft`, not `scrollIntoView`: that walks every
    // scrollable ancestor, so a remote mounting below the fold — or mounting
    // while the host restores its own scroll position — would pull the whole
    // page to the tab strip, or fight the host for it. This moves one box,
    // horizontally, and only when the tab is actually outside it.
    //
    // Rects, not `offsetLeft`: that is measured from `offsetParent`, and
    // nothing on this strip's ancestor chain is positioned — so it would be
    // the body, or whatever positioned element the HOST happens to provide.
    // The tab's page offset would then be added to the scroll: in a host with
    // a sidebar the strip jumps to its end, and a tab off the LEFT edge never
    // comes back, because `left < scrollLeft` is never true. Rects are
    // measured against the viewport for both boxes, so the difference is the
    // distance to move and nothing else.
    //
    // Moved BY that distance rather than set to it, which is also what makes
    // this hold under `dir="rtl"`: there `scrollLeft` counts down from zero
    // in most engines, so any absolute position computed from a left offset
    // is wrong, while a delta is a delta.
    const frame = strip.getBoundingClientRect();
    const box = tab.getBoundingClientRect();
    let next = strip.scrollLeft;
    if (box.left < frame.left) next -= frame.left - box.left;
    else if (box.right > frame.right) next += box.right - frame.right;
    // Bookkeeping, and it is load-bearing: assigning the position it already
    // has is a no-op in every engine, so no scroll event follows it — and a
    // mark left behind for an event that never comes is a mark the reader's
    // next scroll is measured against. Land within a pixel of it and their
    // move is read as ours.
    if (next === strip.scrollLeft) return;
    ourScroll.current = next;
    lastScroll.current = next;
    // `scrollTo` with `instant`, not an assignment: the assignment path obeys
    // CSS `scroll-behavior`, and a host writing `* { scroll-behavior: smooth }`
    // — which does reach this strip, unlike the `html` rule people mean to
    // write — turns our one move into an ANIMATION. The browser then fires a
    // run of scroll events at positions that are not the one we asked for,
    // the first of them marks the reader as having scrolled, and every later
    // correction is suppressed for the life of the tab. `instant` cannot be
    // overridden from CSS at all.
    //
    // Behind a `try`, because `instant` is younger than `scrollTo` itself
    // (Chrome 97, Firefox 97, Safari 15.4) and WebIDL answers an unknown
    // enum value with a TypeError rather than ignoring the member. This runs
    // inside a layout effect: an uncaught throw here takes the React root
    // down, and whether the host wraps this remote in an error boundary is
    // not something it can assume. The assignment is the degradation — and
    // the one thing it gives up, animation, is what `.exact-tabs`'s own
    // `scroll-behavior: auto` already refuses. jsdom, which has no
    // `scrollTo` at all, comes through the same door.
    try {
      strip.scrollTo({ left: next, behavior: "instant" });
    } catch {
      strip.scrollLeft = next;
    }
  }, []);

  const noteScroll = useCallback(() => {
    const strip = stripRef.current;
    if (!strip) return;
    // Consumed whichever branch this event takes, so a mark set by a
    // narrowing can never outlive the event it was set for.
    const clamped = absorbClamp.current;
    absorbClamp.current = false;
    lastScroll.current = strip.scrollLeft;
    // Our own move, coming back to us as an event. Browsers may coalesce a
    // run of them, so this clears the mark on the first one that matches and
    // treats anything further as the reader's.
    if (ourScroll.current !== null && Math.abs(strip.scrollLeft - ourScroll.current) < 1) {
      ourScroll.current = null;
      return;
    }
    // And the browser's own move. When the tabs get narrower than the
    // position the strip is scrolled to, the engine clamps `scrollLeft` and
    // reports it as a scroll — with no mark of ours on it, so it read as the
    // reader scrolling and suppressed every later correction until they
    // changed tab. The whole bar now loses its numbers at once (#536), which
    // is the largest narrowing this strip has and makes that the ordinary
    // path rather than a corner: on a phone the reader who scrolled to
    // Favorites, opened it, and then rotated found the tab they were on off
    // the edge with nothing bringing it back.
    if (clamped) return;
    readerScrolled.current = true;
  }, []);

  // Measured off the tabs rather than `scrollWidth`, which is the same
  // number in a browser and zero in jsdom — this has to be observable in a
  // test, because what it guards against is invisible in one.
  useLayoutEffect(() => {
    const strip = stripRef.current;
    if (!strip) return;
    // `scrollWidth`/`clientWidth` when the engine has them, because their
    // difference IS the scroll range — padding and all. Measured off the
    // tabs instead, the two sides are different boxes: the tabs are content
    // and the strip's rect is its border box, and `.exact-tabs` pads itself
    // by 3px either side for the focus ring (#535). A scroll container's
    // range includes that padding, so the tab-measured range is 6px short —
    // and `keepActiveInView`, which aligns to the border box, parks the
    // strip inside exactly that gap when it brings the last tab into view.
    // Every later narrowing then armed the clamp mark with no clamp coming,
    // and swallowed the reader's next scroll.
    const buttons = strip.querySelectorAll("button");
    const first = buttons[0]?.getBoundingClientRect();
    const last = buttons[buttons.length - 1]?.getBoundingClientRect();
    // From the extremes, not `last.right - first.left`: under `dir="rtl"`
    // the first button in the DOM is the rightmost on screen and that
    // subtraction goes negative, so the strip would stop recognising its own
    // clamps and start reading them as the reader — the bug this guards
    // against, in the layout nobody tests by hand.
    // jsdom reports 0 for both, which is why the fallback exists at all: it
    // lays nothing out, so the tabs' stated rects are the only measurement
    // there is. In a browser these are never 0 for a rendered strip.
    const room = strip.clientWidth || strip.getBoundingClientRect().width;
    const measured = strip.scrollWidth;
    const width =
      measured ||
      (first && last
        ? Math.max(first.right, last.right) - Math.min(first.left, last.left)
        : 0);
    // Only when the position the strip was AT cannot survive the new width.
    // Narrower tabs with the scroll already inside the new range clamp
    // nothing and send no event, and a mark left standing for an event that
    // never comes is one the reader's next scroll is swallowed by — their
    // own move read as the engine's, which is this bug with the two sides
    // swapped.
    //
    // The position it was at, not the one it is at: the engine clamps
    // during layout, so by the time this runs `scrollLeft` is already the
    // clamped value and the question cannot be asked of it.
    // `Math.abs`, for the same reason: RTL counts `scrollLeft` DOWN from
    // zero in most engines, so what matters is the distance from the origin
    // rather than the sign of it.
    if (width < contentWidth.current && Math.abs(lastScroll.current) > Math.max(0, width - room)) {
      absorbClamp.current = true;
    }
    contentWidth.current = width;
    lastScroll.current = strip.scrollLeft;
  });

  // All of them or none: see `barCounts`.
  const numbered = barCounts(tabs, active, counts, activeTabTotal, stateCounts);

  // Not on `[active]` alone: the tab in force can leave the window without
  // being changed. The counts arrive from a request of their own, so a badge
  // appearing on a tab to the LEFT widens it and pushes the active one out —
  // on this strip that is the ordinary case, not a corner one. `labels` is
  // the rendered text of the whole bar, so it changes exactly when a tab's
  // width can have.
  //
  // Read from `numbered`, the same thing the badges are painted from, so
  // there is one answer to "what does the bar say" rather than two. Not for
  // correctness: `numbered` is a function of what `tabCount` returns, so it
  // can never change where a per-tab reading would not. It can fail to
  // change where one would — a Favorites count moving while the bar is
  // unnumbered — and the correction that costs is a no-op anyway, since
  // nothing on screen moved.
  const labels = tabs
    .map((tab) => `${tab.label}:${numbered?.get(tab.value) ?? ""}`)
    .join("|");
  // Changing tab is a fresh intent: it overrides wherever the reader had
  // scrolled the strip to, and starts the next stretch of looking. One case
  // is not the reader's: `TrialMatches` reassigns the active tab during
  // render when the one in force leaves the bar (the host's state adapter
  // appearing, a deep-linked tab dropped). Rare, and it costs them a scroll
  // position rather than anything they typed.
  useLayoutEffect(() => {
    readerScrolled.current = false;
    keepActiveInView();
  }, [keepActiveInView, active]);

  // A relayout is not an intent, so it corrects the position only while the
  // reader has not chosen one of their own. Otherwise a count landing a
  // second after mount drags the strip back from wherever they had scrolled
  // it to — the same interruption this is meant to prevent, in the other
  // direction.
  useLayoutEffect(() => {
    if (!readerScrolled.current) keepActiveInView();
  }, [keepActiveInView, labels]);

  // And the other way the window moves: the host resizing its column, which
  // no prop of ours reports. A ref callback rather than an effect with `[]`,
  // which would be equally correct here — this <nav> is unconditional and
  // lives exactly as long as the component — because the callback cannot be
  // wrong about it: React hands back the node it has, and null on the way
  // out. Without a `ResizeObserver` (jsdom, an old browser) the strip still
  // scrolls on every change of tab, which is the common case.
  const stripCallback = useCallback(
    (node: HTMLElement | null) => {
      observerRef.current?.disconnect();
      observerRef.current = null;
      stripRef.current = node;
      if (!node || typeof ResizeObserver === "undefined") return;
      const observer = new ResizeObserver(() => {
        if (!readerScrolled.current) keepActiveInView();
      });
      observer.observe(node);
      observerRef.current = observer;
    },
    [keepActiveInView],
  );

  // Plain buttons in a <nav>, not role="tablist"/"tab". The ARIA tab pattern
  // promises arrow-key navigation, a roving tabindex, and an associated
  // tabpanel; announcing "tab, 1 of 3" and then not moving on ← / → is worse
  // for a screen-reader user than not claiming the pattern at all. These are
  // filters over one list, and `aria-current` says which one is applied.
  return (
    <nav
      ref={stripCallback}
      onScroll={noteScroll}
      className="exact-tabs"
      aria-label="Filter trials by match status"
    >
      {tabs.map((tab) => {
        const isActive = tab.value === active;
        const count = numbered?.get(tab.value) ?? null;
        const tip = TAB_TOOLTIPS[tab.value];
        const button = (tipId?: string) => (
          <button
            key={tab.value}
            type="button"
            ref={isActive ? activeRef : undefined}
            aria-describedby={tipId}
            aria-current={isActive || undefined}
            // The label and the count are separate elements with no
            // whitespace between them, so the accessible name computes to
            // "Eligible19". Spelled out here instead.
            aria-label={
              count != null
                ? `${tab.label}, ${count} ${count === 1 ? "trial" : "trials"}`
                : tab.label
            }
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
        // Always wrapped, so a tab whose tooltip comes and goes keeps focus.
        return (
          <ActionTooltip key={tab.value} text={tip} align="start">
            {button}
          </ActionTooltip>
        );
      })}
    </nav>
  );
}
