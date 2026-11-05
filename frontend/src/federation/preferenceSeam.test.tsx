/** The preferences-only seam.
 *
 *  A host can be able to answer for the patient's saved search settings and
 *  for nothing else — the standalone widget build is mounted by exactly that
 *  kind of app. Before this, reaching the settings meant handing over a whole
 *  `TrialStateAdapter`, and the tabs and the bookmark gated on that would
 *  come with it, pointing at stores the host does not have.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, fakeState, renderTrialMatches } from "../test/renderTrialMatches";
import type { FilterState } from "./types";
import type { TrialPreferenceStore } from "./state";

/** A host that keeps the settings and nothing else — CB keeps the four
 *  suitability weights on its own user row and has no store for bookmarks. */
const preferenceOnlyHost = (initial: FilterState = {}) => {
  const saved: FilterState[] = [];
  let current = initial;
  const store: TrialPreferenceStore = {
    getPreferences: async () => current,
    savePreferences: async (filters) => {
      saved.push(filters);
      current = filters;
    },
    resetPreferences: async () => {
      current = {};
    },
  };
  return { store, saved };
};

describe("a host that supplies preferences only", () => {
  it("gets no tabs and no bookmark it cannot answer for", async () => {
    // The trap this seam exists to avoid: `state` is what those controls are
    // gated on, so reaching the settings through a stub adapter would render
    // a Favorites tab over a store that does not exist.
    const api = fakeApi();
    const { store } = preferenceOnlyHost();
    renderTrialMatches(api, { preferences: store });

    await screen.findByRole("button", { name: /^Eligible/ });
    expect(screen.queryByRole("button", { name: /^Favorites/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Registered/ })).toBeNull();
    // After a row exists, or the bookmark's absence says nothing: it is
    // rendered inside the cards, and the list is held while the settings
    // are still being read.
    await screen.findByRole("button", { name: "View Trial" });
    expect(screen.queryByRole("button", { name: /favorites$/ })).toBeNull();
  });

  it("is read on arrival, so the reader gets what they saved last time", async () => {
    const api = fakeApi();
    const { store } = preferenceOnlyHost({ searchTitle: "daratumumab" });
    renderTrialMatches(api, { preferences: store });

    // The badge counts filters that differ from the baseline: visible proof
    // the saved set was applied rather than read and dropped.
    await screen.findByRole("button", { name: /Filters \(1\)/ });
    await waitFor(() =>
      expect(
        api.listRequests()[api.listRequests().length - 1].params.searchTitle,
      ).toBe("daratumumab"),
    );
  });

  it("is written to when the reader changes a filter", async () => {
    const api = fakeApi();
    const { store, saved } = preferenceOnlyHost();
    renderTrialMatches(api, { preferences: store });

    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await userEvent.click(
      screen.getByRole("button", { name: /Filter Results|Filters \(/ }),
    );
    await userEvent.type(await screen.findByLabelText("Title"), "myeloma");

    await waitFor(() => expect(saved.length).toBeGreaterThan(0));
    expect(saved[saved.length - 1].searchTitle).toBe("myeloma");
  });
});

describe("a host that changes its mind mid-session", () => {
  it("re-reads from the store that is winning now", async () => {
    // The transport is deliberately blind to a new adapter OBJECT for the
    // same patient — a host writing `state={createPromopState(...)}` inline
    // hands one over on every render. Blind to a different STORE it must not
    // be: writes re-resolve to the winner either way, so without a re-read
    // the reader's filters come from one store while their edits go to the
    // other. `preferenceVersioning` is decided at the same moment, and a
    // store that cannot do conditional writes would keep the adapter that
    // can on the unconditional path for the life of the mount.
    const api = fakeApi();
    const { store } = preferenceOnlyHost({ searchTitle: "from-the-host" });
    const { adapter } = fakeState({
      overrides: { getPreferences: async () => ({ searchTitle: "from-promop" }) },
    });
    const { setProps } = renderTrialMatches(api, { preferences: store });
    await waitFor(() =>
      expect(
        api.listRequests().some((r) => r.params.searchTitle === "from-the-host"),
      ).toBe(true),
    );

    setProps({ state: adapter });

    await waitFor(() =>
      expect(
        api.listRequests().some((r) => r.params.searchTitle === "from-promop"),
      ).toBe(true),
    );
  });
});

describe("a host that supplies both", () => {
  it("is answered from `state`, which already carries the settings", async () => {
    // Not a merge of the two: one store or the other, so a reader cannot end
    // up with filters read from one place and written to another.
    const api = fakeApi();
    const { adapter } = fakeState();
    const fromAdapter = vi.spyOn(adapter, "getPreferences");
    const { store } = preferenceOnlyHost({ country: "Germany" });
    const fromStore = vi.spyOn(store, "getPreferences");

    renderTrialMatches(api, { state: adapter, preferences: store });

    await waitFor(() => expect(fromAdapter).toHaveBeenCalled());
    expect(fromStore).not.toHaveBeenCalled();
  });
});
