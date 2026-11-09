import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import type { WritableFields } from "./writable";
import type { TrialDetailField } from "./types";

const WRITABLE: WritableFields = {
  estimated_glomerular_filtration_rate: {
    kind: "computed",
    writable: false,
    inputs: ["serum_creatinine_level"],
  },
  serum_creatinine_level: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "number",
  },
};

const row = (over: Partial<TrialDetailField> = {}): TrialDetailField => ({
  name: "estimatedGlomerularFiltrationRateMin",
  label: "Estimated Glomerular Filtration Rate Minimum",
  type: "int",
  value: 30,
  ufield: "estimatedGlomerularFiltrationRate",
  upatientField: "estimated_glomerular_filtration_rate",
  ureadonly: false,
  upatientRecomputed: false,
  uvalue: 45,
  utype: "int",
  matchingType: "unknown",
  ...over,
});

const creatinine = (): TrialDetailField => ({
  name: "serumCreatinineLevelAbsMin",
  label: "Serum Creatinine Level",
  type: "float",
  value: 1,
  ufield: "serumCreatinineLevel",
  upatientField: "serum_creatinine_level",
  ureadonly: false,
  upatientRecomputed: false,
  uvalue: 1.2,
  utype: "float",
  matchingType: "unknown",
});

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

describe("computed rows without server subform_details", () => {
  it("builds the inputs from the descriptor and the sibling rows", async () => {
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(detailWith([row(), creatinine()]));
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();

    expect(
      await screen.findByRole("button", {
        name: "Change what Estimated Glomerular Filtration Rate Minimum is worked out from",
      }),
    ).toBeInTheDocument();
  });

  it("edits the input through the subform and writes its canonical field", async () => {
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(detailWith([row(), creatinine()]));
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();

    await userEvent.click(
      screen.getByRole("button", {
        name: "Change what Estimated Glomerular Filtration Rate Minimum is worked out from",
      }),
    );
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Edit Serum Creatinine Level",
      }),
    );
    const input = await screen.findByRole("textbox", { name: "Serum Creatinine Level" });
    await userEvent.clear(input);
    await userEvent.type(input, "1.3");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(state.record.serum_creatinine_level).toBe(1.3),
    );
  });
});
