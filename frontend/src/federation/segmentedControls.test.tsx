/** The two segmented controls at the head of the list — view mode and sort.
 *
 *  Both were something else before: a single toggle button that named the
 *  action ("Map") rather than the view, and a native `<select>`. What is
 *  worth pinning is what changed for the reader — the state is now said out
 *  loud, every order names itself, and the keyboard did not get worse in the
 *  trade.
 */
import { describe, expect, it } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, renderTrialMatches } from "../test/renderTrialMatches";

const seg = (name: string) => screen.getByRole("radio", { name });

describe("the view mode control", () => {
  it("says which view is showing, which the old toggle never did", async () => {
    // The button it replaces was labelled with what the NEXT press would do,
    // so "Map" was on screen exactly when the map was not.
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "List view" });
    expect(seg("List view")).toBeChecked();
    expect(seg("Map view")).not.toBeChecked();

    await userEvent.click(seg("Map view"));

    await screen.findByText("Where these trials are");
    expect(seg("Map view")).toBeChecked();
    expect(seg("List view")).not.toBeChecked();
  });

  it("is one tab stop, and its arrows both move and choose", async () => {
    // What a `<select>` gave for free, and what a row of plain buttons would
    // have taken away: three more stops on the way to anything after them.
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "List view" });
    seg("List view").focus();

    await userEvent.keyboard("{ArrowRight}");

    expect(seg("Map view")).toHaveFocus();
    expect(seg("Map view")).toBeChecked();
    expect(seg("List view")).toHaveAttribute("tabindex", "-1");

    // Wrapping, as the radiogroup pattern asks: past the end is the start.
    await userEvent.keyboard("{ArrowRight}");
    expect(seg("List view")).toHaveFocus();
    expect(seg("List view")).toBeChecked();
  });

  it("wraps backwards too, from the first segment to the last", async () => {
    // The other direction is a separate line of arithmetic — `index - 1` is
    // -1 at the first segment, and a modulo alone leaves it there. Without
    // this the backward wrap can go dead silently: the key does nothing, no
    // error, and every other keyboard test still passes.
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "List view" });
    seg("List view").focus();

    await userEvent.keyboard("{ArrowLeft}");

    expect(seg("Map view")).toHaveFocus();
    expect(seg("Map view")).toBeChecked();
  });

  it("really is one tab stop: the next tab leaves the group", async () => {
    // The test above reads `tabindex="-1"` off the segment not chosen, which
    // is the mechanism, not the behaviour. This presses the key.
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "List view" });
    const group = screen.getByRole("radiogroup", { name: "View" });
    seg("List view").focus();

    await userEvent.tab();

    expect(seg("Map view")).not.toHaveFocus();
    expect(group.contains(document.activeElement)).toBe(false);
  });
});

describe("the controls row", () => {
  it("puts the sort where the keyboard reaches it last, as the eye does", async () => {
    // Without a ResizeObserver (this environment) the row is the narrow one,
    // where the sort paints on a second line under the actions. CSS `order`
    // would paint it there and leave its tab stop on row 1, sending the
    // keyboard down to the sort and back up to the actions (WCAG 2.4.3), so
    // the sort is WRITTEN after them instead.
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "List view" });
    const order = [
      seg("List view"),
      screen.getByRole("button", { name: "Explore Trials" }),
      screen.getByRole("button", { name: "Export CSV" }),
      screen.getByRole("button", { name: "Filter Results" }),
      seg("Sort By Suitability Score"),
    ];
    for (const [i, node] of order.slice(1).entries()) {
      // DOCUMENT_POSITION_FOLLOWING: each one comes after the one before it.
      expect(order[i].compareDocumentPosition(node) & 4).toBe(4);
    }

    // And the tab sequence agrees: from the view mode (one stop for the whole
    // group) forward through the actions, and only then the sort.
    seg("List view").focus();
    const stops: (string | null)[] = [];
    for (let i = 1; i < order.length; i++) {
      await userEvent.tab();
      stops.push(document.activeElement?.textContent ?? null);
    }
    expect(stops).toEqual([
      "Explore Trials",
      "Export CSV",
      "Filter Results",
      "Sort By Suitability Score",
    ]);
  });
});

describe("the sort control", () => {
  it("offers CB's three orders and sends the one that is picked", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    expect(seg("Sort By Suitability Score")).toBeChecked();

    await userEvent.click(seg("Sort by Distance"));

    await waitFor(() => expect(api.listRequests().length).toBe(2));
    const last = api.listRequests()[1];
    expect(last.params.sort).toBe("distance");
    expect(seg("Sort by Distance")).toBeChecked();
  });

  it("does not fetch the orders the arrows pass through", async () => {
    // Arrows choose as they move, which is the pattern and what a `<select>`
    // does — but each stop on the way is a list request and a full re-rank
    // under the reader. Walking two segments must cost one of each.
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("radio", { name: "Sort By Suitability Score" });
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    seg("Sort By Suitability Score").focus();

    await userEvent.keyboard("{ArrowRight}{ArrowRight}");

    expect(seg("Sort by Distance")).toBeChecked();
    await waitFor(() => expect(api.listRequests().length).toBe(2));
    expect(api.listRequests()[1].params.sort).toBe("distance");
    // And nothing arrives late for the order that was only passed through.
    await new Promise((r) => setTimeout(r, 400));
    expect(api.listRequests().map((r) => r.params.sort)).toEqual([
      "goodnessScore",
      "distance",
    ]);
  });

  it("shows an order the host asked for that CB does not offer", async () => {
    // The server takes more sort keys than CB offers. A control that dropped
    // one would read as "sorted by suitability" over a list that is not.
    const api = fakeApi();
    renderTrialMatches(api, { initialFilters: { sort: "phase" } });
    expect(await screen.findByRole("radio", { name: "Sorted by phase" })).toBeChecked();
    expect(seg("Sort By Suitability Score")).not.toBeChecked();
  });

  it("jumps to the first and last order with Home and End", async () => {
    // Both keys are part of the pattern and neither is reachable any other
    // way: the arrows walk, these two leap. Untested, they are two lines
    // that can be deleted without a single suite going red.
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    seg("Sort By Suitability Score").focus();

    await userEvent.keyboard("{End}");

    expect(seg("Sort by Distance")).toHaveFocus();
    expect(seg("Sort by Distance")).toBeChecked();

    await userEvent.keyboard("{Home}");

    expect(seg("Sort By Suitability Score")).toHaveFocus();
    expect(seg("Sort By Suitability Score")).toBeChecked();
  });

  it("walks the orders without scrolling the tooltip that is open", async () => {
    // A tooltip too tall for the screen scrolls from the arrow keys of the
    // control it belongs to, and those are the same keys that move through
    // this control. Moving takes focus to the next segment, which is a
    // different tooltip, so the open box stays where it was rather than
    // scrolling under a reader who was walking the orders.
    const api = fakeApi();
    renderTrialMatches(api);
    const first = await screen.findByRole("radio", { name: "Sort By Suitability Score" });
    // Keyboard focus, which is what opens the box (a click's does not).
    await userEvent.keyboard("{Shift}");
    act(() => first.focus());
    const box = document.getElementById(first.getAttribute("aria-describedby")!)!;
    expect(box).toHaveClass("is-open");
    // jsdom lays nothing out, so the overflow the scroller looks for is stated.
    Object.defineProperty(box, "scrollHeight", { value: 500, configurable: true });
    Object.defineProperty(box, "clientHeight", { value: 100, configurable: true });

    await userEvent.keyboard("{ArrowDown}");

    expect(box.scrollTop).toBe(0);
    expect(seg("Sort by Matching Score")).toHaveFocus();
    expect(seg("Sort by Matching Score")).toBeChecked();
  });
});
