import { describe, expect, it } from "vitest";

import {
  SORT_OPTIONS,
  TABS,
  tabsFor,
  getPageNumbers,
  sortOptionsFor,
  tabCount,
  tabValueForType,
} from "./listChrome";

describe("getPageNumbers", () => {
  // Ported from CB `pages/Trials.tsx`; these lock the port so the two
  // paginations cannot drift apart silently.
  it("lists every page while there are seven or fewer", () => {
    expect(getPageNumbers(1, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(getPageNumbers(4, 4)).toEqual([1, 2, 3, 4]);
  });

  it("elides the tail when the current page is near the start", () => {
    expect(getPageNumbers(1, 20)).toEqual([1, 2, "…", 20]);
    expect(getPageNumbers(3, 20)).toEqual([1, 2, 3, 4, "…", 20]);
  });

  it("elides the head when the current page is near the end", () => {
    expect(getPageNumbers(20, 20)).toEqual([1, "…", 19, 20]);
  });

  it("elides both sides in the middle", () => {
    expect(getPageNumbers(10, 20)).toEqual([1, "…", 9, 10, 11, "…", 20]);
  });

  it("returns nothing when there are no pages", () => {
    expect(getPageNumbers(1, 0)).toEqual([]);
  });

  it("never repeats a page number", () => {
    // The window around the current page can otherwise collide with the
    // pinned first/last entries — at page 2 of 8 the window starts at 1,
    // and at page 7 it ends at 8.
    for (let total = 8; total <= 12; total++) {
      for (let page = 1; page <= total; page++) {
        const numbers = getPageNumbers(page, total).filter(
          (entry): entry is number => entry !== "…",
        );
        expect(new Set(numbers).size, `page ${page} of ${total}`).toBe(
          numbers.length,
        );
      }
    }
  });

  it("keeps the numbers ascending", () => {
    for (let page = 1; page <= 20; page++) {
      const numbers = getPageNumbers(page, 20).filter(
        (entry): entry is number => entry !== "…",
      );
      expect([...numbers].sort((a, b) => a - b), `page ${page}`).toEqual(numbers);
    }
  });

  it("always offers the current page as a target", () => {
    for (let page = 1; page <= 20; page++) {
      expect(getPageNumbers(page, 20), `page ${page}`).toContain(page);
    }
  });
});

describe("tabCount", () => {
  const counts = { eligible: 7, potential: 12 };

  it("sums both buckets for the default tab", () => {
    expect(tabCount("eligible_and_potential", counts, 19)).toBe(19);
  });

  it("reads each bucket for its own tab", () => {
    expect(tabCount("eligible", counts, null)).toBe(7);
    expect(tabCount("potential", counts, null)).toBe(12);
  });

  it("returns null — not zero — when the server sent no counts", () => {
    // The server omits `tabCounts` when it had no patient context, or under
    // `?type=all`, because no per-row verdict was computed. Rendering "0"
    // there would state a clinical result nobody produced.
    expect(tabCount("eligible", undefined, 40)).toBeNull();
    expect(tabCount("potential", undefined, 40)).toBeNull();
  });

  it("falls back to the response total only for the tab being listed", () => {
    // The caller passes the total for the active tab and null for the rest,
    // so an inactive tab cannot be labelled with the active tab's number.
    expect(tabCount("eligible_and_potential", undefined, 40)).toBe(40);
    expect(tabCount("eligible_and_potential", undefined, null)).toBeNull();
  });
});

describe("TABS", () => {
  it("sends no type for the default tab", () => {
    // `eligible_and_potential` is a no-op server-side, identical to sending
    // no `type` at all — so the wire carries nothing rather than a value
    // that looks like it narrows something.
    expect(TABS[0].value).toBe("eligible_and_potential");
    expect(TABS[0].param).toBeUndefined();
  });

  it("offers no tab the server would reject", () => {
    // `favorites` and `my_trials` are 400s until PROMOP-backed state lands
    // in phase 2 (EXACT #417). A tab that cannot work must not be rendered.
    const params = TABS.map((tab) => tab.param);
    expect(params).not.toContain("favorites");
    expect(params).not.toContain("my_trials");
    expect(params).not.toContain("not_eligible");
  });
});

describe("tabValueForType", () => {
  // The host's `initialFilters.type` is public API. Before this, the tab
  // state overwrote it on the first render, so a host mounting the remote
  // to show the potential subset silently got the default tab instead.
  it("selects the tab that sends the requested type", () => {
    expect(tabValueForType("eligible")).toBe("eligible");
    expect(tabValueForType("potential")).toBe("potential");
  });

  it("falls back to the default tab for no type", () => {
    expect(tabValueForType(undefined)).toBe("eligible_and_potential");
  });

  it("falls back for a server value that has no tab", () => {
    // `all` is accepted by the server but is not offered as a tab (it takes
    // the admin branch, which skips the eligibility filter). Selecting a
    // tab that does not exist would leave the bar with nothing highlighted.
    expect(tabValueForType("all")).toBe("eligible_and_potential");
    expect(tabValueForType("favorites")).toBe("eligible_and_potential");
  });
});

describe("sortOptionsFor", () => {
  it("returns CB's three for a value it already offers", () => {
    expect(sortOptionsFor("goodnessScore")).toEqual(SORT_OPTIONS);
  });

  it("surfaces a value the list does not offer", () => {
    // The server accepts more sort keys than CB shows. A controlled
    // <select> whose value matches no <option> renders blank while the list
    // is genuinely sorted that way — the control would misreport itself.
    const options = sortOptionsFor("updated");
    expect(options).toHaveLength(SORT_OPTIONS.length + 1);
    // Indexed rather than `.at(-1)`: the lib is ES2020, where `Array.at` is
    // only declared transitively, and the build should not lean on that.
    expect(options[options.length - 1]).toEqual({
      value: "updated",
      label: "Sorted by updated",
    });
  });
});

describe("tabsFor", () => {
  it("offers CB's match tabs without a state adapter", () => {
    expect(tabsFor(false).map((t) => t.value)).toEqual([
      "eligible_and_potential",
      "eligible",
      "potential",
    ]);
  });

  it("adds Registered and Favorites once there is somewhere to keep them", () => {
    expect(tabsFor(true).map((t) => t.value)).toEqual([
      "eligible_and_potential",
      "eligible",
      "potential",
      "registered",
      "favorites",
    ]);
  });

  it("marks the state tabs so they are narrowed by ids, not by ?type=", () => {
    // The server rejects `?type=favorites` outright; these are narrowed by
    // a `trial_ids` list instead.
    const byIds = tabsFor(true).filter((t) => t.needsState);
    expect(byIds.map((t) => t.value)).toEqual(["registered", "favorites"]);
    expect(byIds.every((t) => t.param === undefined)).toBe(true);
  });
});

describe("tabCount for the state tabs", () => {
  const counts = { eligible: 7, potential: 12 };

  it("counts what the patient saved, not what the matcher found", () => {
    // A bookmark stays a bookmark whether or not the trial still matches
    // today, so the matcher's counts cannot answer for these tabs.
    expect(tabCount("favorites", counts, null, { favorites: 3 })).toBe(3);
    expect(tabCount("registered", counts, null, { registered: 2 })).toBe(2);
  });

  it("shows nothing while the ids are unknown", () => {
    expect(tabCount("favorites", counts, 40, undefined)).toBeNull();
    expect(tabCount("favorites", counts, 40, {})).toBeNull();
  });

  it("shows zero when the patient really has none", () => {
    // Distinct from unknown: `0` is an answer, and the tab should say so.
    expect(tabCount("favorites", counts, null, { favorites: 0 })).toBe(0);
  });
});
