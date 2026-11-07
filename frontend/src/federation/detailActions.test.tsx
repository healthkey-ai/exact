// Host-owned detail actions. EXACT draws the affordance; the host owns what it
// opens. Nothing is drawn without a callback, and the context-dependent ones
// (MCL authoring, Missing Attributes) also require the trial to be a real
// candidate for them.
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeApi, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import type {
  HighRiskMclCriteriaBreakdown,
  TrialDetailField,
} from "./types";

const row = (over: Partial<TrialDetailField> = {}): TrialDetailField => ({
  name: "hemoglobin",
  label: "Hemoglobin",
  type: "number",
  value: 12,
  matchingType: "matched",
  ...over,
});

const mcl: HighRiskMclCriteriaBreakdown = {
  aggregate: "matched",
  minCount: 1,
  matchedCount: 1,
  required: [],
  excluded: [],
  sufficientAny: [],
};

const detail = (
  fields: TrialDetailField[],
  breakdown: HighRiskMclCriteriaBreakdown | null = null,
) =>
  trialDetail(1, {
    details: { trialEligibilityAttributes: fields },
    highRiskMclCriteriaBreakdown: breakdown,
  } as never);

const open = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
  // The back link is in the shell, before the detail query resolves. Wait for
  // the loaded title so an absence assertion cannot pass on a page that has
  // not painted its actions yet.
  await screen.findByRole("heading", { level: 1, name: "Trial 1" });
};

describe("host-owned detail actions", () => {
  it("draws each affordance and reports the trial to the host", async () => {
    const api = fakeApi();
    api.setDetail(
      detail([row(), row({ name: "missing", label: "Missing", matchingType: "unknown" })], mcl),
    );
    const onCompareToSoc = vi.fn();
    const onAuthorHighRiskMcl = vi.fn();
    const onShareTrial = vi.fn();
    const onOpenMissingAttributes = vi.fn();
    renderTrialMatches(api, {
      onCompareToSoc,
      onAuthorHighRiskMcl,
      onShareTrial,
      onOpenMissingAttributes,
    });

    await open();

    await userEvent.click(screen.getByRole("button", { name: "Share trial" }));
    expect(onShareTrial).toHaveBeenCalledWith(1);

    await userEvent.click(
      screen.getByRole("button", { name: "Compare to Standard of Care" }),
    );
    expect(onCompareToSoc).toHaveBeenCalledWith(1);

    await userEvent.click(
      await screen.findByRole("button", { name: "Missing attributes" }),
    );
    expect(onOpenMissingAttributes).toHaveBeenCalledWith(1);

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Author high-risk MCL criteria" }),
      ).toBeInTheDocument(),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Author high-risk MCL criteria" }),
    );
    expect(onAuthorHighRiskMcl).toHaveBeenCalledWith(1);
  });

  it("draws none of them without a callback", async () => {
    const api = fakeApi();
    api.setDetail(
      detail([row(), row({ name: "missing", label: "Missing", matchingType: "unknown" })], mcl),
    );
    renderTrialMatches(api);

    await open();
    // The MCL panel waits on the criteria catalog; without it the authoring
    // button is absent for the wrong reason.
    await screen.findByRole("heading", { name: "High-Risk MCL Criteria" });

    expect(screen.queryByRole("button", { name: "Share trial" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Compare to Standard of Care" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Missing attributes" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Author high-risk MCL criteria" }),
    ).toBeNull();
  });

  it("withholds MCL authoring when the trial gates on no criteria", async () => {
    const api = fakeApi();
    api.setDetail(detail([row()]));
    const onAuthorHighRiskMcl = vi.fn();
    renderTrialMatches(api, { onAuthorHighRiskMcl });

    await open();
    expect(
      screen.queryByRole("button", { name: "Author high-risk MCL criteria" }),
    ).toBeNull();
  });

  it("withholds Missing Attributes when nothing is missing", async () => {
    const api = fakeApi();
    api.setDetail(detail([row()]));
    const onOpenMissingAttributes = vi.fn();
    renderTrialMatches(api, { onOpenMissingAttributes });

    await open();
    expect(screen.queryByRole("button", { name: "Missing attributes" })).toBeNull();
  });
});
