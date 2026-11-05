// #568 — a saved trial the patient no longer qualifies for.
//
// The server stopped dropping those from a `trial_ids` search, because
// dropping them left the Favorites badge reading 1 over the words "No trials
// found". Listing them is only half an answer: a row that looks like every
// other row says the patient matches. These tests are about the other half —
// that the card says where the matcher stands, and says it on that row only.
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeApi, renderTrialMatches, trial } from "../test/renderTrialMatches";

const MARK = /You may not meet this trial's eligibility criteria/;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("a saved trial the matcher would drop", () => {
  it("says so on the card", async () => {
    const api = fakeApi({ results: [trial(1, { matchingType: "not_eligible" })] });
    renderTrialMatches(api);
    expect(await screen.findByText(MARK)).toBeTruthy();
  });

  it("says nothing on a trial that still matches", async () => {
    const api = fakeApi({ results: [trial(1, { matchingType: "eligible" })] });
    renderTrialMatches(api);
    await screen.findByText("Trial 1");
    expect(screen.queryByText(MARK)).toBeNull();
  });

  it("says nothing on a potential one either", async () => {
    // `potential` means the patient left a field blank, not that they
    // conflict. Marking it would tell a reader they fail a trial they have
    // simply not finished answering for.
    const api = fakeApi({ results: [trial(1, { matchingType: "potential" })] });
    renderTrialMatches(api);
    await screen.findByText("Trial 1");
    expect(screen.queryByText(MARK)).toBeNull();
  });

  it("marks the failing row and only the failing row", async () => {
    // The Favorites tab's real shape: some bookmarks still match and some do
    // not. A mark that painted the whole list would be worse than none.
    const api = fakeApi({
      count: 1,
      itemsTotalCount: 2,
      results: [
        trial(1, { matchingType: "not_eligible" }),
        trial(2, { matchingType: "eligible" }),
      ],
    });
    renderTrialMatches(api);
    await waitFor(() => expect(screen.getAllByText(/^Trial [12]$/).length).toBe(2));
    expect(screen.getAllByText(MARK).length).toBe(1);

    const marked = screen.getByText(MARK).closest(".exact-card");
    expect(marked?.textContent).toContain("Trial 1");
  });

  it("draws the score the verdict comes with, not a green one", async () => {
    // The pairing the server sends: `not_eligible` with `matchScore: 0`, the
    // pair the matcher returns for that verdict. The card must render it as
    // given — it used to receive 100 here, because the list's score counts
    // which criteria could be EVALUATED and never compares values.
    const api = fakeApi({
      results: [trial(1, { matchingType: "not_eligible", matchScore: 0 })],
    });
    renderTrialMatches(api);
    await screen.findByText(MARK);
    const card = screen.getByText(MARK).closest(".exact-card")!;
    expect(card.textContent).toContain("0%");
    expect(card.textContent).not.toContain("100%");
  });

  it("leaves the suitability score alone", async () => {
    // A different quantity — benefit, distance, burden, risk — and one the
    // server can still stand behind for a trial the patient fails.
    const api = fakeApi({
      results: [
        trial(1, { matchingType: "not_eligible", matchScore: 0, goodnessScore: 62 }),
      ],
    });
    renderTrialMatches(api);
    await screen.findByText(MARK);
    const card = screen.getByText(MARK).closest(".exact-card")!;
    expect(card.textContent).toContain("62%");
  });

  it("still shows the match score on a row that matches", async () => {
    const api = fakeApi({
      results: [trial(1, { matchingType: "eligible", matchScore: 100 })],
    });
    renderTrialMatches(api);
    await screen.findByText("Trial 1");
    const card = screen.getByText("Trial 1").closest(".exact-card")!;
    expect(card.textContent).toContain("100%");
  });

  it("says nothing when the response names no patient", async () => {
    // `matchingType: null` is "nobody was judged" (#456), not "judged and
    // failed". A mark there would be a verdict the request never asked for.
    const api = fakeApi({ results: [trial(1, { matchingType: null })] });
    renderTrialMatches(api);
    await screen.findByText("Trial 1");
    expect(screen.queryByText(MARK)).toBeNull();
  });
});
