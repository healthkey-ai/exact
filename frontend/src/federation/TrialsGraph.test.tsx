/** "Explore Trials" — the panel, and when it asks for anything.
 *
 *  The graph costs a second full matcher run over the same search, so the
 *  test that matters most is the one about NOT fetching it.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, fakeState, renderTrialMatches, trial } from "../test/renderTrialMatches";

const graphRequests = (api: ReturnType<typeof fakeApi>) =>
  api.requests.filter((r) => r.url.includes("/trials-graph/graph/"));

describe("Explore Trials", () => {
  it("asks for nothing until the reader opens it", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    expect(graphRequests(api)).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Explore Trials" }));
    await waitFor(() => expect(graphRequests(api).length).toBe(1));
    // POST, because the patient travels in a body and the GET form is
    // reachable only where `?person_id=` is allowed.
    expect(graphRequests(api)[0].method).toBe("post");
  });

  it("shows each trial with the requirements it asks about", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("button", { name: "Explore Trials" }));

    await screen.findByText(/Which requirements you meet/);
    // The text list carries the same content as the picture, which is what a
    // screen reader and a narrow window get.
    await screen.findByText(/Met: Disease/);
    await screen.findByText(/Not known: ECOG/);
  });

  it("opens the trial's detail page from the map", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("button", { name: "Explore Trials" }));

    const link = await screen.findByRole("button", { name: "Trial 1" });
    await userEvent.click(link);

    await screen.findByText("Back to all trials");
  });

  it("says there is nothing to explore rather than drawing an empty map", async () => {
    const api = fakeApi({ results: [], count: 0, itemsTotalCount: 0 });
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("button", { name: "Explore Trials" }));

    await screen.findByText(/nothing to explore yet/);
  });

  it("says so when the map cannot be built", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    api.failNextWith(500);

    await userEvent.click(screen.getByRole("button", { name: "Explore Trials" }));
    await screen.findByRole("alert");
  });
});

describe("Explore Trials — the text beneath the picture", () => {
  it("describes each trial by ITS answers, not by the banding", async () => {
    // A node is banded by its worst answer across every trial on screen, which
    // is right for the picture. Printed under one trial it would say "Not met:
    // ECOG" beneath a trial whose ECOG the patient does meet.
    const api = fakeApi({ results: [trial(1), trial(2)] });
    api.setGraph([
      {
        trialId: 1,
        match: { matched: [{ patientField: "ecog", label: "ECOG" }], missing: [], notMatched: [] },
      },
      {
        trialId: 2,
        match: { matched: [], missing: [], notMatched: [{ patientField: "ecog", label: "ECOG" }] },
      },
    ]);
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("button", { name: "Explore Trials" }));

    // Trial 1 met it; trial 2 did not. Both lines exist.
    await screen.findByText("Met: ECOG");
    await screen.findByText("Not met: ECOG");
  });
});

describe("Explore Trials on a state tab", () => {
  it("draws the bookmarks rather than the whole corpus", async () => {
    // Favorites narrows by `trial_ids` alone — its `type` is undefined — so a
    // graph request without them answers with everything, under a tab that
    // says Favorites.
    const api = fakeApi();
    renderTrialMatches(api, { state: fakeState({ favorites: ["1"] }).adapter });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() =>
      expect(api.listRequests().at(-1)?.body).toMatchObject({ trial_ids: ["1"] }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Explore Trials" }));
    await waitFor(() => expect(graphRequests(api).length).toBe(1));
    expect(graphRequests(api)[0].body).toMatchObject({ trial_ids: ["1"] });
  });
});

describe("Explore Trials when the saved ids are unavailable", () => {
  it("refuses rather than mapping the whole corpus under a state tab", async () => {
    // With no ids the graph's cache key is the default tab's key, so a query
    // that is merely disabled still lets react-query hand back the cached
    // whole-corpus map under a tab that says "Favorites" — or, with nothing
    // cached, sit on "Building the map…" for ever.
    const api = fakeApi();
    const state = fakeState({
      overrides: {
        listFavoriteIds: vi.fn(async () => {
          throw new Error("promop unreachable");
        }),
      },
    });
    renderTrialMatches(api, { state: state.adapter });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    // Build a cached map on the default tab first, so there is something to
    // hand back wrongly.
    await userEvent.click(screen.getByRole("button", { name: "Explore Trials" }));
    await screen.findByText(/Which requirements you meet/);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));

    const explore = screen.getByRole("button", { name: "Explore Trials" });
    await waitFor(() => expect(explore).toBeDisabled());
    expect(explore).toHaveAttribute("title", expect.stringContaining("aren't available"));
    expect(screen.queryByText(/Which requirements you meet/)).toBeNull();
  });
});
