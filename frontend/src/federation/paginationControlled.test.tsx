// Controlled pagination: the host owns the page number (`?page=` in its URL)
// and this component reports changes instead of moving itself.
//
// The reset-on-sort case is the one that matters most: the page has to go
// back to 1 in the SAME render that changes the query, or the first request
// for the new sort goes out as page 3 and DRF answers a 404.
import { describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { FilterState } from "./types";
import { fakeApi, fakeState, renderTrialMatches } from "../test/renderTrialMatches";

const listed = (api: ReturnType<typeof fakeApi>) => api.listRequests();

describe("controlled pagination", () => {
  it("asks for the page the host names", async () => {
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    renderTrialMatches(api, { page: 2, onPageChange: () => {} });

    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].params.page).toBe("2");
  });

  it("reports a click instead of moving, until the host renders the new page", async () => {
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    const onPageChange = vi.fn();
    const view = renderTrialMatches(api, { page: 2, onPageChange });

    await waitFor(() => expect(listed(api).length).toBe(1));
    await userEvent.click(await screen.findByRole("button", { name: "3" }));

    expect(onPageChange).toHaveBeenCalledWith(3);
    // The prop still says 2, so the list must not move on its own.
    expect(listed(api).length).toBe(1);

    view.setProps({ page: 3 });
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.page).toBe("3");
  });

  it("resets to page 1 when the query changes, without requesting the old page first", async () => {
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    const onPageChange = vi.fn();
    const view = renderTrialMatches(api, { page: 3, onPageChange });

    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].params.page).toBe("3");

    await userEvent.click(screen.getByRole("radio", { name: "Sort by Matching Score" }));

    await waitFor(() => expect(onPageChange).toHaveBeenCalledWith(1));
    await waitFor(() => expect(listed(api).length).toBe(2));
    const second = listed(api)[1];
    expect(second.params.sort).toBe("matchScore");
    // Page 1 is omitted on the wire; the request never went out as page 3 of
    // a list that may not have three pages.
    expect(second.params.page).toBeUndefined();

    view.setProps({ page: 1 });
    await waitFor(() => expect(listed(api).length).toBe(2));
  });

  it("keeps the host page when saved preferences hydrate", async () => {
    // Hydration changes the query key, but it is not the reader changing a
    // filter: a `?page=3` reload with a saved sponsor must stay on page 3.
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    const onPageChange = vi.fn();
    const state = fakeState({
      overrides: {
        getPreferences: vi.fn(async () => {
          await Promise.resolve();
          return { sponsor: "Acme" };
        }),
      },
    });

    renderTrialMatches(api, {
      page: 3,
      onPageChange,
      state: state.adapter,
      stateIdentity: "patient-1",
    });

    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].params.page).toBe("3");
    expect(listed(api)[0].params.sponsor).toBe("Acme");
    expect(onPageChange).not.toHaveBeenCalled();
  });

  it("does not swallow the next filter change when a late saved set changes nothing", async () => {
    // The grace window opens the list before a slow preferences read lands.
    // That late answer repeats the value the panel already had, so the query
    // key does not move — and the hydration exception must still expire. Left
    // standing, it would skip the page reset for the reader's NEXT change.
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    const onPageChange = vi.fn();
    let deliver!: (value: FilterState) => void;
    const state = fakeState({
      overrides: {
        getPreferences: vi.fn(
          () =>
            new Promise<FilterState>((resolve) => {
              deliver = resolve;
            }),
        ),
      },
    });

    renderTrialMatches(api, {
      page: 3,
      onPageChange,
      state: state.adapter,
      stateIdentity: "patient-1",
      initialFilters: { sponsor: "Acme" },
    });

    // The list is not held past the grace, so this request goes out while the
    // preferences read is still open.
    await waitFor(() => expect(listed(api).length).toBe(1), { timeout: 2000 });
    expect(listed(api)[0].params.page).toBe("3");

    await act(async () => {
      deliver({ sponsor: "Acme" });
      await Promise.resolve();
    });

    await userEvent.click(screen.getByRole("radio", { name: "Sort by Matching Score" }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    // If the exception leaked, this would still be page 3.
    expect(listed(api)[1].params.page).toBeUndefined();
  });

  it("does not report the same reset twice when the host changes its callback", async () => {
    // A host passing an inline callback hands over a new function on every
    // render. That is not a new reset event, and reporting it again would be
    // a second navigation (or a loop) for one page change.
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    const first = vi.fn();
    const second = vi.fn();
    const view = renderTrialMatches(api, { page: 3, onPageChange: first });

    await waitFor(() => expect(listed(api).length).toBe(1));
    await userEvent.click(screen.getByRole("radio", { name: "Sort by Matching Score" }));
    await waitFor(() => expect(first).toHaveBeenCalledWith(1));

    view.setProps({ onPageChange: second });
    expect(second).not.toHaveBeenCalled();
  });

  it("reports one reset when a patient switch also moves the view key", async () => {
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    const onPageChange = vi.fn();
    const state = fakeState();
    const view = renderTrialMatches(api, {
      page: 2,
      onPageChange,
      state: state.adapter,
      stateIdentity: "patient-a",
      patientInfo: { disease: "multiple myeloma", country: "US" },
    });
    await waitFor(() => expect(listed(api).length).toBeGreaterThan(0));

    view.setProps({
      stateIdentity: "patient-b",
      patientInfo: { disease: "multiple myeloma", country: "CA" },
    });

    await waitFor(() => expect(onPageChange).toHaveBeenCalledWith(1));
    expect(onPageChange.mock.calls.filter(([page]) => page === 1)).toHaveLength(1);
  });

  it("treats a half contract as none: page without onPageChange stays internal", async () => {
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    renderTrialMatches(api, { page: 2 });

    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].params.page).toBeUndefined();

    await userEvent.click(await screen.findByRole("button", { name: "2" }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.page).toBe("2");
  });
});
