import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import type { WritableFields } from "./writable";
import type { TrialDetailField } from "./types";

const row = (over: Partial<TrialDetailField> = {}): TrialDetailField => ({
  name: "preExistingConditionsExcluded",
  label: "Pre Existing Conditions Excluded",
  type: "multiselect",
  value: ["cardiacIssues"],
  ufield: "preExistingConditionCategories",
  upatientField: "pre_existing_condition_categories",
  ureadonly: false,
  upatientRecomputed: false,
  uvalue: [],
  utype: "multiselect",
  matchingType: "unknown",
  ...over,
});

const DESCRIPTOR: WritableFields = {
  pre_existing_condition_categories: {
    kind: "explicit-answer",
    writable: false,
    answerable: true,
    target: "patient_record",
    endpoint: "preexisting-conditions-answer",
    actions: ["confirm_none", "set_categories", "withdraw"],
    action_labels: {
      confirm_none: "No pre-existing conditions",
      set_categories: "Save these conditions",
      withdraw: "Withdraw answer",
    },
    value_kind: "string",
    multiple: true,
    options: [
      { value: "cardiacIssues", label: "Cardiac Issues" },
      { value: "infections", label: "Infections" },
    ],
  },
};

function detailWith(fields: TrialDetailField[]) {
  return trialDetail(1, {
    details: { trialEligibilityAttributes: fields },
  } as never);
}

const openDetail = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
};

function renderWithAnswer() {
  const answer = vi.fn(async () => undefined);
  const state = fakeState({
    writable: DESCRIPTOR,
    overrides: { explicitAnswers: { answer } },
  });
  const api = fakeApi();
  api.setDetail(detailWith([row()]));
  renderTrialMatches(api, { state: state.adapter });
  return { answer };
}

describe("explicit answers", () => {
  it("offers the attestation when the host serves the writer", async () => {
    renderWithAnswer();
    await openDetail();

    expect(
      await screen.findByRole("button", { name: "Answer Pre Existing Conditions Excluded" }),
    ).toBeInTheDocument();
  });

  it("confirms none through the dedicated adapter, never a field save", async () => {
    const { answer } = renderWithAnswer();
    await openDetail();
    await userEvent.click(
      screen.getByRole("button", { name: "Answer Pre Existing Conditions Excluded" }),
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "No pre-existing conditions" }),
    );

    expect(answer).toHaveBeenCalledWith(
      "pre_existing_condition_categories",
      "confirm_none",
      undefined,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("saves selected categories through the same adapter", async () => {
    const { answer } = renderWithAnswer();
    await openDetail();
    await userEvent.click(
      screen.getByRole("button", { name: "Answer Pre Existing Conditions Excluded" }),
    );
    await userEvent.click(await screen.findByLabelText("Infections"));
    await userEvent.click(
      screen.getByRole("button", { name: "Save these conditions" }),
    );

    expect(answer).toHaveBeenCalledWith(
      "pre_existing_condition_categories",
      "set_categories",
      { categories: ["infections"] },
    );
  });

  it("keeps the dialog open and shows the failure when the adapter rejects", async () => {
    const answer = vi.fn(async () => {
      throw new Error("The answer could not be saved.");
    });
    const state = fakeState({
      writable: DESCRIPTOR,
      overrides: { explicitAnswers: { answer } },
    });
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();
    await userEvent.click(
      screen.getByRole("button", { name: "Answer Pre Existing Conditions Excluded" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "No pre-existing conditions" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The answer could not be saved.",
    );
  });

  it("does not offer the attestation without a writer", async () => {
    const state = fakeState({ writable: DESCRIPTOR });
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();

    expect(
      screen.queryByRole("button", { name: "Answer Pre Existing Conditions Excluded" }),
    ).toBeNull();
  });
});
