/** The tab in force, when the strip scrolls.
 *
 *  It only scrolls at all since the strip stopped widening the page, and the
 *  tab that is applied can start off the right edge — a reader whose saved
 *  tab is Favorites would see a row that looks truncated, with no sign that
 *  the one they are on is out there. Browsers scroll a FOCUSED control into
 *  view on their own; nothing does it for a selected one.
 *
 *  jsdom lays nothing out, so the geometry is stated — and stated the way a
 *  browser reports it, which is the point. `offsetLeft` counts from
 *  `offsetParent`, and nothing on this strip's ancestor chain is positioned,
 *  so in the host it counts from whatever the HOST has positioned: in ht-phr,
 *  from beyond a sidebar. Every tab is therefore laid out here at a page
 *  offset, and the rects — which are what the code reads — at their true
 *  on-screen position. A test that stamps `offsetLeft` as if the strip were
 *  the origin defines that bug out of existence; this one leaves it in.
 */
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Tabs } from "./Tabs";
import { tabsFor } from "./listChrome";
import type { TabCounts } from "./types";
import { fakeApi, fakeState, renderTrialMatches } from "../test/renderTrialMatches";

const rect = (left: number, width: number) =>
  ({
    left,
    right: left + width,
    width,
    x: left,
    top: 0,
    bottom: 0,
    height: 0,
    y: 0,
    toJSON: () => ({}),
  }) as DOMRect;

/** Lay the strip out: a 200px window over tabs 100px wide, in order, the
 *  whole row pushed right by whatever the host holds to the left of it. */
const layOut = (hostOffset = 250) => {
  const strip = document.querySelector(".exact-tabs") as HTMLElement;
  Object.defineProperty(strip, "clientWidth", { configurable: true, value: 200 });
  strip.getBoundingClientRect = () => rect(hostOffset, 200);
  const tabs = [...strip.querySelectorAll("button")];
  tabs.forEach((tab, index) => {
    // What a browser would report: the distance from the host's positioned
    // ancestor, which no amount of scrolling this strip changes.
    Object.defineProperty(tab, "offsetLeft", {
      configurable: true,
      value: hostOffset + index * 100,
    });
    Object.defineProperty(tab, "offsetWidth", { configurable: true, value: 100 });
    // And where it actually is on screen, which is what moves.
    tab.getBoundingClientRect = () => rect(hostOffset + index * 100 - strip.scrollLeft, 100);
  });
  return { strip, tabs };
};

const mount = async () => {
  const api = fakeApi();
  const state = fakeState({ favorites: ["1"] });
  renderTrialMatches(api, { state: state.adapter });
  await screen.findByRole("button", { name: /^Eligible/ });
};

describe("the tab strip", () => {
  it("brings a tab past its right edge into view", async () => {
    await mount();
    const { strip } = layOut();
    strip.scrollLeft = 0;

    // Favorites is the third tab: 200-300 into a strip 200 wide.
    await userEvent.click(screen.getByRole("button", { name: /^Favorites/ }));

    await waitFor(() => expect(strip.scrollLeft).toBe(100));
  });

  it("measures from the strip, not from what the host has positioned", async () => {
    // The same click against a host that holds nothing to the left. If the
    // arithmetic counted from `offsetParent`, the two would disagree by the
    // width of the sidebar — and the version with one would scroll to the end
    // of the strip and stay there, because a tab off the LEFT edge would
    // never read as being off it.
    await mount();
    const { strip } = layOut(0);
    strip.scrollLeft = 0;

    await userEvent.click(screen.getByRole("button", { name: /^Favorites/ }));

    await waitFor(() => expect(strip.scrollLeft).toBe(100));
  });

  it("brings one back that is off the left edge", async () => {
    await mount();
    const { strip } = layOut();
    // Away first: nothing has changed yet that the strip reacts to, and
    // Eligible is the tab it starts on.
    await userEvent.click(screen.getByRole("button", { name: /^Favorites/ }));
    strip.scrollLeft = 150;

    await userEvent.click(screen.getByRole("button", { name: /^Eligible/ }));

    await waitFor(() => expect(strip.scrollLeft).toBe(0));
  });

  it("leaves the strip alone when the tab is already in view", async () => {
    await mount();
    const { strip } = layOut();
    // Through a scroll EVENT, which is how a browser reports the reader
    // moving it: assigning `scrollLeft` fires nothing, here or anywhere.
    strip.scrollLeft = 40;
    fireEvent.scroll(strip);

    // The second tab sits at 100-200 within the strip, inside the 40-240 of
    // it that is on screen.
    await userEvent.click(screen.getByRole("button", { name: /^Registered/ }));

    await waitFor(() => expect(screen.getByRole("button", { name: /^Registered/ })).toBeTruthy());
    expect(strip.scrollLeft).toBe(40);
  });

  it("does not correct a strip the reader moved, on a relayout of the real list", async () => {
    // The guard against overruling the reader is pinned in the standalone
    // describes below; this is it against the component the host mounts.
    // Changing the sort takes the counts away and brings them back, which
    // is a change of the tab LABELS and so a correction the strip would
    // otherwise make — over a position the reader chose.
    const api = fakeApi();
    const state = fakeState({ favorites: ["1"] });
    renderTrialMatches(api, { state: state.adapter });
    await screen.findByRole("button", { name: /^Eligible/ });
    const { strip } = layOut();
    strip.scrollLeft = 120;
    fireEvent.scroll(strip);

    await userEvent.click(screen.getByRole("radio", { name: "Sort by Matching Score" }));
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(1));
    await new Promise((resolve) => setTimeout(resolve, 60));

    expect(strip.scrollLeft).toBe(120);
  });

  it("moves the strip and nothing above it", async () => {
    // `scrollIntoView` walks every scrollable ancestor: a remote mounting
    // below the fold would pull the host's page to the tab strip.
    const calls: Element[] = [];
    const real = Element.prototype.scrollIntoView;
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value: function scrollIntoView(this: Element) {
        calls.push(this);
      },
    });
    // Put back before leaving, so the rest of the file is not measuring a
    // prototype this test replaced.
    onTestFinished(() => {
      Object.defineProperty(Element.prototype, "scrollIntoView", {
        configurable: true,
        writable: true,
        value: real,
      });
    });
    await mount();
    layOut();

    await userEvent.click(screen.getByRole("button", { name: /^Favorites/ }));

    expect(calls).toEqual([]);
  });
});

/** The same strip on its own, where the widths are the thing being changed.
 *
 *  A count arriving is a RELAYOUT: the badge widens the tab it lands on and
 *  everything to its right moves. Nothing about the tab in force changes, so
 *  an effect watching only that never runs, and the reader is left on a tab
 *  that has quietly slid out of the window.
 *
 *  Laid out on the prototype rather than on the nodes, because the first
 *  measurement happens while the strip is being mounted — after `render`
 *  returns there is nothing left to stub.
 */
describe("the tab strip, when the tabs themselves move", () => {
  const COUNTS: TabCounts = { eligible: 300, potential: 69 };
  /** The strip's own width, which belongs to the host's column. */
  let windowWidth = 200;
  /** Mutable, so a re-render lands on the new geometry the way a relayout
   *  would: the badge widens its tab and the rest of the bar shifts. */
  let widths: number[] = [];
  let original: typeof Element.prototype.getBoundingClientRect;

  beforeEach(() => {
    widths = [100, 100, 100];
    windowWidth = 200;
    original = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function measured(this: Element) {
      const strip = this.closest(".exact-tabs") as HTMLElement | null;
      if (this.classList.contains("exact-tabs")) return rect(0, windowWidth);
      if (!strip || !(this instanceof HTMLButtonElement)) return original.call(this);
      const index = [...strip.querySelectorAll("button")].indexOf(this);
      if (index < 0) return original.call(this);
      const left = widths.slice(0, index).reduce((sum, w) => sum + w, 0);
      return rect(left - strip.scrollLeft, widths[index]);
    };
    // jsdom has no `Element.prototype.scrollTo`, so without this every test
    // here would take the fallback — the one path a browser never runs.
    Element.prototype.scrollTo = function scrollTo(this: Element, options: ScrollToOptions) {
      if (typeof options?.left === "number") this.scrollLeft = options.left;
    } as typeof Element.prototype.scrollTo;
  });

  afterEach(() => {
    Element.prototype.getBoundingClientRect = original;
    delete (Element.prototype as Partial<Element>).scrollTo;
  });

  const bar = (counts?: TabCounts) => (
    <Tabs
      tabs={tabsFor(true)}
      active="favorites"
      onChange={() => {}}
      counts={counts}
      activeTabTotal={null}
      stateCounts={{ favorites: 1, registered: 0 }}
    />
  );

  const registered = (counts?: TabCounts) => (
    <Tabs
      tabs={tabsFor(true)}
      active="registered"
      onChange={() => {}}
      counts={counts}
      activeTabTotal={null}
      stateCounts={{ favorites: 1, registered: 0 }}
    />
  );

  const strip = () => document.querySelector(".exact-tabs") as HTMLElement;

  it("brings the tab in force back when a count widens the one before it", async () => {
    const view = render(bar());
    // Favorites is the third tab, 200-300 in a 200-wide window, so mounting
    // has already brought it to the right edge.
    expect(strip().scrollLeft).toBe(100);

    // "Eligible & Potential 369" arrives and the tab it lands on grows.
    widths[0] = 160;
    view.rerender(bar(COUNTS));

    await waitFor(() => expect(strip().scrollLeft).toBe(160));
  });

  it("does not mistake its own scrolling for the reader's", async () => {
    // A browser fires `scroll` for a programmatic scroll too, so the mark
    // has to be told apart by WHERE it landed. Read the strip's own move as
    // the reader's and every correction after the first is suppressed — the
    // feature works once per tab and then quietly stops.
    const view = render(bar());
    expect(strip().scrollLeft).toBe(100);
    fireEvent.scroll(strip());

    widths[0] = 160;
    view.rerender(bar(COUNTS));

    await waitFor(() => expect(strip().scrollLeft).toBe(160));
  });

  it("goes to the tab the reader picks, wherever they had scrolled to", async () => {
    // Picking a tab is an intent of its own, and it outranks where the strip
    // happened to be left.
    const view = render(bar());
    strip().scrollLeft = 0;
    fireEvent.scroll(strip());

    view.rerender(
      <Tabs
        tabs={tabsFor(true)}
        active="registered"
        onChange={() => {}}
        activeTabTotal={null}
        stateCounts={{ favorites: 1, registered: 0 }}
      />,
    );

    // Registered is the second tab, 100-200, which the 0-200 window holds.
    await waitFor(() => expect(strip().scrollLeft).toBe(0));
    // And on to one that it does not.
    view.rerender(bar());
    await waitFor(() => expect(strip().scrollLeft).toBe(100));
  });

  it("starts listening again once the reader picks a tab", async () => {
    // The mark is dropped when they choose a tab, or the strip stops
    // correcting itself for the rest of the session the moment it is
    // scrolled once.
    const view = render(bar());
    strip().scrollLeft = 0;
    fireEvent.scroll(strip());

    view.rerender(registered());
    // Registered, 100-200, is inside the 0-200 window: nothing to do yet.
    await waitFor(() => expect(strip().scrollLeft).toBe(0));

    widths[0] = 160;
    view.rerender(registered(COUNTS));

    await waitFor(() => expect(strip().scrollLeft).toBe(60));
  });

  it("does not claim a scroll it never made", async () => {
    // The mark that says "this move was ours" is left for an event that is
    // about to arrive. A check that moves NOTHING sends no event — so if it
    // leaves a mark anyway, the mark is still standing whenever the reader
    // next scrolls, and a landing within a pixel of it reads as ours.
    // Sub-pixel positions are ordinary: a trackpad, or the tail of a fling.
    const view = render(bar());
    expect(strip().scrollLeft).toBe(100);
    fireEvent.scroll(strip());

    // A relayout that moves nothing: the labels change, the widths do not.
    view.rerender(bar(COUNTS));
    await new Promise((resolve) => setTimeout(resolve, 0));

    // The reader nudges the strip by less than a pixel.
    strip().scrollLeft = 100.4;
    fireEvent.scroll(strip());

    // And now a count lands on the tab BEFORE the one in force and widens
    // it, which would otherwise be a correction.
    widths[0] = 160;
    view.rerender(bar({ eligible: 301, potential: 69 }));

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(strip().scrollLeft).toBe(100.4);
  });

  it("asks for an instant scroll, whatever the host's scroll-behavior", () => {
    // `scrollLeft = x` obeys CSS `scroll-behavior`, and a host writing
    // `* { scroll-behavior: smooth }` makes one move an animation whose
    // intermediate scroll events are indistinguishable from the reader's.
    // The first of them marks them as having scrolled, and every later
    // correction is suppressed for the life of the tab.
    const calls: ScrollToOptions[] = [];
    Element.prototype.scrollTo = function scrollTo(this: Element, options: ScrollToOptions) {
      calls.push(options);
      if (typeof options.left === "number") this.scrollLeft = options.left;
    } as typeof Element.prototype.scrollTo;

    render(bar());

    expect(calls).toEqual([{ left: 100, behavior: "instant" }]);
    expect(strip().scrollLeft).toBe(100);
  });

  it("does not drag the strip back from where the reader put it", async () => {
    const view = render(bar());
    expect(strip().scrollLeft).toBe(100);

    // The reader scrolls back to read the first tab. Favorites is off the
    // right edge now because THEY put it there, which is not a thing to fix.
    strip().scrollLeft = 0;
    fireEvent.scroll(strip());

    widths[0] = 160;
    view.rerender(bar(COUNTS));

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(strip().scrollLeft).toBe(0);
  });
});

/** The other way the window moves: the host's column, which no prop reports.
 *
 *  jsdom has no `ResizeObserver`, so without one standing here this path is
 *  not merely untested — it does not run at all, in any test in the suite.
 */
describe("the tab strip, when the host resizes the column", () => {
  class FakeResizeObserver {
    static live: FakeResizeObserver[] = [];
    observed: Element[] = [];
    disconnected = false;
    constructor(private callback: ResizeObserverCallback) {
      FakeResizeObserver.live.push(this);
    }
    observe(element: Element) {
      this.observed.push(element);
    }
    unobserve() {}
    disconnect() {
      this.disconnected = true;
    }
    /** The strip's width is read from the DOM, not from the entry, so what
     *  the browser would deliver here carries no information we use.
     *
     *  Reported on demand, where a real observer also delivers one
     *  observation on `observe()` — asynchronously, after the commit. The
     *  extra call that makes at mount is a no-op either way, since the
     *  effect has already put the tab where it belongs. */
    report() {
      act(() => this.callback([], this as unknown as ResizeObserver));
    }
    static current() {
      const open = FakeResizeObserver.live.filter((observer) => !observer.disconnected);
      return open[open.length - 1];
    }
  }

  let widths: number[] = [];
  let windowWidth = 200;
  let original: typeof Element.prototype.getBoundingClientRect;

  beforeEach(() => {
    widths = [100, 100, 100];
    windowWidth = 200;
    FakeResizeObserver.live = [];
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    original = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function measured(this: Element) {
      const strip = this.closest(".exact-tabs") as HTMLElement | null;
      if (this.classList.contains("exact-tabs")) return rect(0, windowWidth);
      if (!strip || !(this instanceof HTMLButtonElement)) return original.call(this);
      const index = [...strip.querySelectorAll("button")].indexOf(this);
      if (index < 0) return original.call(this);
      const left = widths.slice(0, index).reduce((sum, w) => sum + w, 0);
      return rect(left - strip.scrollLeft, widths[index]);
    };
  });

  afterEach(() => {
    Element.prototype.getBoundingClientRect = original;
    vi.unstubAllGlobals();
  });

  const bar = () => (
    <Tabs
      tabs={tabsFor(true)}
      active="favorites"
      onChange={() => {}}
      activeTabTotal={null}
      stateCounts={{ favorites: 1, registered: 0 }}
    />
  );

  const strip = () => document.querySelector(".exact-tabs") as HTMLElement;

  it("watches the strip itself", () => {
    render(bar());
    expect(FakeResizeObserver.current().observed).toEqual([strip()]);
  });

  it("brings the tab in force back when the column narrows", () => {
    render(bar());
    expect(strip().scrollLeft).toBe(100);

    windowWidth = 150;
    FakeResizeObserver.current().report();

    // Favorites ends at 300; a 150-wide window has to start at 150.
    expect(strip().scrollLeft).toBe(150);
  });

  it("leaves a column change alone when the reader has scrolled", () => {
    render(bar());
    strip().scrollLeft = 0;
    fireEvent.scroll(strip());

    windowWidth = 150;
    FakeResizeObserver.current().report();

    expect(strip().scrollLeft).toBe(0);
  });

  it("stops watching a strip it no longer has", () => {
    // An observer left pointed at a detached node reports its 0x0 for ever
    // after. Here the <nav> goes when the component does, so what this holds
    // is the ref callback's own contract: React hands it null on the way out
    // and the observer goes with it.
    const view = render(bar());
    const observer = FakeResizeObserver.current();
    view.unmount();
    expect(observer.disconnected).toBe(true);
  });
});
