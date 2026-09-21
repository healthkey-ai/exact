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
import { WIDE_CONTROLS_ROW } from "./listChrome";

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
    act(() => {
      this.callback(
        [{ contentRect: { width } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    });
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

    FakeResizeObserver.current().report(WIDE_CONTROLS_ROW);

    expect(isWide()).toBe(true);

    // And back, so the measurement is read on every delivery rather than
    // latched the first time it is true.
    FakeResizeObserver.current().report(WIDE_CONTROLS_ROW - 1);

    expect(isWide()).toBe(false);
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
    first.report(WIDE_CONTROLS_ROW);
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

    // And it still answers: the reader's column did not change, so the wide
    // layout the row arrived in is the one they get back.
    now.report(WIDE_CONTROLS_ROW);
    await waitFor(() => expect(isWide()).toBe(true));
  });
});
