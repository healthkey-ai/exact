// `features.hideFavorites`: a navigator impersonating a patient must not get
// Favorites or a bookmark control. The host knows it is impersonating; EXACT
// must not infer it from data, and Registered interest stays available.
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeApi, fakeState, renderTrialMatches } from "../test/renderTrialMatches";

const listed = (api: ReturnType<typeof fakeApi>) => api.listRequests();

const open = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
  await screen.findByRole("heading", { level: 1, name: "Trial 1" });
};

describe("features.hideFavorites", () => {
  it("hides the Favorites tab and every bookmark control", async () => {
    const api = fakeApi();
    const state = fakeState();
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      features: { hideFavorites: true },
    });

    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(screen.queryByRole("button", { name: /^Favorites/ })).toBeNull();
    // Registered interest is not part of hiding Favorites.
    expect(screen.getByRole("button", { name: /^Registered/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add Trial 1 to favorites" }),
    ).toBeNull();

    await open();
    expect(
      screen.queryByRole("button", { name: /Trial 1 (to|from) favorites/ }),
    ).toBeNull();
  });

  it("shows both without the flag", async () => {
    const api = fakeApi();
    const state = fakeState();
    renderTrialMatches(api, { state: state.adapter, stateIdentity: "patient-1" });

    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(screen.getByRole("button", { name: /^Favorites/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add Trial 1 to favorites" }),
    ).toBeInTheDocument();
  });

  it("reacts when the host starts and stops impersonating", async () => {
    const api = fakeApi();
    const state = fakeState();
    const view = renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
    });

    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(screen.getByRole("button", { name: /^Favorites/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add Trial 1 to favorites" }),
    ).toBeInTheDocument();

    view.setProps({ features: { hideFavorites: true } });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /^Favorites/ })).toBeNull(),
    );
    expect(
      screen.queryByRole("button", { name: "Add Trial 1 to favorites" }),
    ).toBeNull();

    view.setProps({ features: {} });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^Favorites/ })).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Add Trial 1 to favorites" }),
    ).toBeInTheDocument();
  });

  it("falls back from an active Favorites tab when impersonation starts", async () => {
    const api = fakeApi();
    const state = fakeState({ favorites: ["1"] });
    const view = renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
    });

    await waitFor(() => expect(listed(api).length).toBe(1));
    const favoritesTab = screen.getByRole("button", { name: /^Favorites/ });
    await userEvent.click(favoritesTab);
    await waitFor(() => expect(favoritesTab).toHaveAttribute("aria-current", "true"));

    view.setProps({ features: { hideFavorites: true } });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /^Favorites/ })).toBeNull(),
    );
    // The reader lands on a tab that still exists, rather than on one the bar
    // cannot name.
    expect(screen.getByRole("button", { name: /^Eligible/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("hides the detail bookmark when the flag arrives with the trial open", async () => {
    const api = fakeApi();
    const state = fakeState();
    const view = renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
    });

    await open();
    expect(
      screen.getByRole("button", { name: "Add Trial 1 to favorites" }),
    ).toBeInTheDocument();

    view.setProps({ features: { hideFavorites: true } });
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /Trial 1 (to|from) favorites/ }),
      ).toBeNull(),
    );

    view.setProps({ features: {} });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add Trial 1 to favorites" }),
      ).toBeInTheDocument(),
    );
  });

  it("stays quiet about a failed favorites read it no longer uses", async () => {
    const api = fakeApi();
    const state = fakeState({
      overrides: {
        listFavoriteIds: vi.fn(async () => {
          throw new Error("favorites unavailable");
        }),
      },
    });
    const view = renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
    });

    await waitFor(() => expect(listed(api).length).toBe(1));
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load your favorites/)).toBeInTheDocument(),
    );

    view.setProps({ features: { hideFavorites: true } });
    await waitFor(() =>
      expect(screen.queryByText(/Couldn't load your favorites/)).toBeNull(),
    );
  });
});
