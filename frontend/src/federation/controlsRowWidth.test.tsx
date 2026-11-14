/** The controls row measures itself, and jsdom has no `ResizeObserver`.
 *
 *  Every other test in the suite therefore sees the narrow row and only the
 *  narrow row: the wide layout, and the observer that turns it on, are
 *  unreachable without standing one up. This file stands one up, because the
 *  bug that lived here was invisible from the narrow side — the wide layout
 *  worked on arrival and died the first time a reader opened a trial.
 */
import { describe, expect, it, afterEach, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, renderTrialMatches } from "../test/renderTrialMatches";
import { wideControlsRow } from "./listChrome";

/** CB's three orders — what every test here renders unless it says otherwise. */
const WIDE = wideControlsRow(3);

/** Records what it was pointed at, so a test can ask whether the thing being
 *  watched is the thing on screen. */
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
  /** What the browser would deliver when the column is this wide. */
  report(width: number) {
    act(() => this.deliver(width));
  }
  /** The same delivery, NOT flushed: the observer is not a React event, so
   *  what it schedules is committed later and the page is live in between. */
  deliver(width: number) {
    this.callback(
      [{ contentRect: { width } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
  static current() {
    const open = FakeResizeObserver.live.filter((o) => !o.disconnected);
    return open[open.length - 1];
  }
}

const install = () => {
  FakeResizeObserver.live = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
};

afterEach(() => {
  vi.unstubAllGlobals();
  FakeResizeObserver.live = [];
});

/** The narrow row is the one that wraps the sort in its own full-width row. */
const isWide = () => document.querySelector(".exact-list__sort") === null;

describe("the controls row's width", () => {
  it("puts the sort beside the actions once the row is wide enough", async () => {
    install();
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "List view" });
    expect(isWide()).toBe(false);

    FakeResizeObserver.current().report(WIDE);

    expect(isWide()).toBe(true);

    // And back, so the measurement is read on every delivery rather than
    // latched the first time it is true.
    FakeResizeObserver.current().report(WIDE - 1);

    expect(isWide()).toBe(false);
  });

  it("keeps the keyboard on the sort when the row moves it", async () => {
    // The two slots are two DOM nodes, so the move is an unmount: focus on
    // the old one goes to <body>, and the reader's next Tab starts at the top
    // of the page. Hosts resize on their own — a sidebar collapsing is
    // enough — so this is not a case only a person dragging a window hits.
    install();
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "List view" });
    const before = screen.getByRole("radio", { name: "Sort By Suitability Score" });
    before.focus();

    FakeResizeObserver.current().report(WIDE);

    expect(isWide()).toBe(true);
    const after = screen.getByRole("radio", { name: "Sort By Suitability Score" });
    // A different node — this is a move, not a re-render of the same one.
    expect(after).not.toBe(before);
    expect(after).toHaveFocus();
  });

  it("leaves the keyboard alone when the move is not under it", async () => {
    // Focus is only restored to the control that lost it. Stealing it for a
    // reader who was reading a card would be its own bug.
    install();
    const api = fakeApi();
    renderTrialMatches(api);
    const filter = await screen.findByRole("button", { name: "Filter Results" });
    filter.focus();

    FakeResizeObserver.current().report(WIDE);

    expect(isWide()).toBe(true);
    expect(filter).toHaveFocus();
  });

  it("does not take back focus the reader has moved on from", async () => {
    // The move is decided when the width arrives and applied on a later
    // commit — an observer is not a React event. A host animating a sidebar
    // delivers widths for a quarter of a second, and the reader is not
    // frozen for it: whatever they Tab or click to in between keeps focus.
    install();
    const api = fakeApi();
    renderTrialMatches(api);
    const sort = await screen.findByRole("radio", { name: "Sort By Suitability Score" });
    sort.focus();
    const filter = screen.getByRole("button", { name: "Filter Results" });

    FakeResizeObserver.current().deliver(WIDE);
    filter.focus();
    await act(async () => {});

    expect(isWide()).toBe(true);
    expect(filter).toHaveFocus();
  });

  it("asks for more room when the host asked for an order CB does not list", async () => {
    // `sortOptionsFor` adds a fourth segment for a server order CB does not
    // offer. A width measured on three then reads as "there is room", the
    // four segments share it, and their labels — held to one line by the
    // wide layout — are clipped by the group's own overflow.
    install();
    const api = fakeApi();
    renderTrialMatches(api, { initialFilters: { sort: "phase" } });
    await screen.findByRole("radio", { name: "Sorted by phase" });

    FakeResizeObserver.current().report(WIDE);

    expect(isWide()).toBe(false);

    FakeResizeObserver.current().report(wideControlsRow(4));

    expect(isWide()).toBe(true);
  });

  it("keeps watching the row that is on screen after a trial is opened", async () => {
    // Opening a trial returns the detail page from the same component, so the
    // row unmounts while the component does not. Watching set up once, on
    // mount, then holds a detached node for the rest of the session: the row
    // it reports on is gone, and the one the reader is looking at is watched
    // by nobody. The layout is stuck on whatever it said last, however wide
    // the host's column becomes.
    install();
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "List view" });
    const first = FakeResizeObserver.current();
    first.report(WIDE);
    expect(isWide()).toBe(true);

    await userEvent.click((await screen.findAllByRole("button", { name: "View Trial" }))[0]);
    await screen.findByRole("button", { name: "Back to all trials" });
    // The row left with the list, and what watched it went with it.
    expect(first.disconnected).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Back to all trials" }));
    await screen.findByRole("radio", { name: "List view" });

    const now = FakeResizeObserver.current();
    expect(now).toBeDefined();
    const watching = now.observed[now.observed.length - 1];
    expect(document.contains(watching)).toBe(true);
    expect(watching).toBe(document.querySelector(".exact-list__controls"));

    // And it still answers — asked for a width it is not already showing,
    // since a report that changes nothing cannot tell a live observer from
    // a dead one.
    now.report(WIDE - 1);
    await waitFor(() => expect(isWide()).toBe(false));
    now.report(WIDE);
    await waitFor(() => expect(isWide()).toBe(true));
  });
});
