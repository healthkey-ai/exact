/** The high-risk MCL panel (#4408).
 *
 *  Rendered through `TrialMatches` rather than in isolation, because the
 *  panel's job is to appear for the trials that carry a breakdown and to stay
 *  away from the ones that do not — and that decision is made on the detail
 *  page, not inside the component.
 */
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, renderTrialMatches } from "../test/renderTrialMatches";
import type { HighRiskMclCriteriaBreakdown } from "./types";

const openDetail = async (api: ReturnType<typeof fakeApi>) => {
  await waitFor(() => expect(api.listRequests().length).toBe(1));
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
};

const breakdown = (
  overrides: Partial<HighRiskMclCriteriaBreakdown> = {},
): HighRiskMclCriteriaBreakdown => ({
  aggregate: "matched",
  minCount: 1,
  matchedCount: 1,
  required: [
    { code: "tp53_mutation", status: "matched" },
    { code: "del17p", status: "not_matched" },
  ],
  excluded: [],
  sufficientAny: [],
  ...overrides,
});

describe("the high-risk MCL panel", () => {
  it("stays away from a trial that gates on no criteria", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await openDetail(api);
    expect(screen.queryByText("High-Risk MCL Criteria")).toBeNull();
    // …and asks for no option catalog it has nothing to label with.
    expect(api.requests.some((r) => r.url.includes("form-settings"))).toBe(false);
  });

  it("names each criterion from the catalog, not from its code", async () => {
    const api = fakeApi();
    api.setDetail({ highRiskMclCriteriaBreakdown: breakdown() });
    renderTrialMatches(api);
    await openDetail(api);

    await screen.findByText("High-Risk MCL Criteria");
    await screen.findByText("TP53 mutation");
    await screen.findByText("del(17p)");
    // The code itself is an identifier, and never the label.
    expect(screen.queryByText("tp53_mutation")).toBeNull();
  });

  it("says how many of the required criteria are needed, and how many are met", async () => {
    // The aggregate verdict alone cannot separate "1 of 3" from "3 of 3",
    // and to a reader deciding whether to call, those are different trials.
    const api = fakeApi();
    api.setDetail({
      highRiskMclCriteriaBreakdown: breakdown({ minCount: 2, matchedCount: 1, aggregate: "not_matched" }),
    });
    renderTrialMatches(api);
    await openDetail(api);

    await screen.findByText("Requires at least 2 of these — you have 1");
  });

  it("reads an excluded criterion the way the reader means it, not the way the server reports it", async () => {
    // The server reports in eligibility terms, so on an EXCLUDED criterion
    // `matched` means "confirmed absent, good" and `not_matched` means
    // "present, ruled out". Printing the raw status word here would tell the
    // reader the exact opposite of the truth.
    const api = fakeApi();
    api.setDetail({
      highRiskMclCriteriaBreakdown: breakdown({
        required: [],
        excluded: [
          { code: "blastoid", status: "matched" },
          { code: "ki67_30", status: "not_matched" },
        ],
      }),
    });
    renderTrialMatches(api);
    await openDetail(api);

    const clear = (await screen.findByText("Blastoid morphology")).closest("li");
    expect(clear).toHaveTextContent("you are clear of this");
    const rulesOut = screen.getByText("Ki-67 >= 30%").closest("li");
    expect(rulesOut).toHaveTextContent("you have this — it rules this trial out");
  });

  it("says a missing value is unknown rather than absent", async () => {
    const api = fakeApi();
    api.setDetail({
      highRiskMclCriteriaBreakdown: breakdown({
        required: [{ code: "tp53_mutation", status: "unknown" }],
        aggregate: "unknown",
      }),
    });
    renderTrialMatches(api);
    await openDetail(api);

    const row = (await screen.findByText("TP53 mutation")).closest("li");
    expect(row).toHaveTextContent("not known from your data");
    await screen.findByText(/data this rule needs is missing/);
  });

  it("does not treat an unmet alternative as a failure", async () => {
    // Any one of them qualifies, so the others being absent costs nothing.
    // Marking them in red put two failure marks under a heading saying exactly
    // that, next to a verdict saying the patient qualifies.
    const api = fakeApi();
    api.setDetail({
      highRiskMclCriteriaBreakdown: breakdown({
        required: [],
        sufficientAny: [
          { code: "tp53_mutation", status: "matched" },
          { code: "ki67_30", status: "not_matched" },
        ],
      }),
    });
    renderTrialMatches(api);
    await openDetail(api);

    const unmet = (await screen.findByText("Ki-67 >= 30%")).closest("li");
    expect(unmet).toHaveTextContent("you do not have this");
    expect(unmet?.className).toContain("is-neutral");
    expect(unmet?.className).not.toContain("is-bad");
  });

  it("separates a confirmed absence from a gap in the data", async () => {
    // #4399 keeps these apart on the server, and a patient who was sequenced
    // and is negative should not read the same sentence as one who was never
    // tested.
    const api = fakeApi();
    api.setDetail({
      highRiskMclCriteriaBreakdown: breakdown({
        required: [
          { code: "tp53_mutation", status: "not_matched" },
          { code: "del17p", status: "unknown" },
        ],
      }),
    });
    renderTrialMatches(api);
    await openDetail(api);

    expect((await screen.findByText("TP53 mutation")).closest("li")).toHaveTextContent(
      "you do not have this",
    );
    expect(screen.getByText("del(17p)").closest("li")).toHaveTextContent(
      "not known from your data",
    );
  });

  it("presents the two inclusion lists as alternatives, because the server ORs them", async () => {
    // With both lists populated the required block is one route of two. Headed
    // "Requires", it told a patient who qualified through the alternatives
    // that they had failed something the server never held them to.
    const api = fakeApi();
    api.setDetail({
      highRiskMclCriteriaBreakdown: breakdown({
        minCount: 2,
        matchedCount: 1,
        sufficientAny: [{ code: "ki67_30", status: "matched" }],
      }),
    });
    renderTrialMatches(api);
    await openDetail(api);

    await screen.findByText("Either — at least 2 of these — you have 1");
    await screen.findByText("Or — any one of these on its own");
  });

  it("renders a breakdown that arrives without one of the lists", async () => {
    // A remote deployed ahead of the API, a gateway that prunes nulls, a
    // hand-rolled host fixture. There is no ErrorBoundary above this, so a
    // throw here takes the whole federated tree down and leaves the host with
    // a blank trials area rather than a missing panel.
    const api = fakeApi();
    api.setDetail({
      highRiskMclCriteriaBreakdown: {
        aggregate: "matched",
        required: [{ code: "tp53_mutation", status: "matched" }],
      } as never,
    });
    renderTrialMatches(api);
    await openDetail(api);

    await screen.findByText("High-Risk MCL Criteria");
    await screen.findByText("TP53 mutation");
  });

  it("waits for the catalog rather than showing raw codes first", async () => {
    // The catalog fetch is gated on the breakdown having arrived, so it always
    // resolves a round-trip later than the panel could paint. Without the wait
    // the code fallback would be the NORMAL first frame and every reader would
    // watch `tp53_mutation` turn into "TP53 mutation".
    const api = fakeApi();
    api.holdFormSettings();
    api.setDetail({ highRiskMclCriteriaBreakdown: breakdown() });
    renderTrialMatches(api);
    await openDetail(api);

    // The page itself is there; the panel is not, and neither is a raw code.
    await screen.findByText("Trial Eligibility Attributes");
    expect(screen.queryByText("High-Risk MCL Criteria")).toBeNull();
    expect(screen.queryByText("tp53_mutation")).toBeNull();
  });

  it("falls back to the code when the catalog does not name it", async () => {
    // Better an identifier than a label the catalog never wrote: these are
    // clinical names, and inventing one by title-casing a code is a mislabel.
    const api = fakeApi();
    api.setDetail({
      highRiskMclCriteriaBreakdown: breakdown({
        required: [{ code: "some_new_code", status: "matched" }],
      }),
    });
    renderTrialMatches(api);
    await openDetail(api);

    await screen.findByText("some_new_code");
  });
});
