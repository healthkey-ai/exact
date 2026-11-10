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

const creatinine = (over: Partial<TrialDetailField> = {}): TrialDetailField => ({
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
  ...over,
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

  it("prefers the writable absolute row over a ULN ratio row", async () => {
    const uln = creatinine({
      name: "serumCreatinineLevelUlnMax",
      label: "Serum Creatinine Level ULN Maximum",
      ureadonly: true,
      uvalue: 2.5,
    });
    const abs = creatinine();
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(detailWith([row(), uln, abs]));
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();

    await userEvent.click(
      screen.getByRole("button", {
        name: "Change what Estimated Glomerular Filtration Rate Minimum is worked out from",
      }),
    );
    expect(
      within(screen.getByRole("dialog")).queryByText(
        "Serum Creatinine Level ULN Maximum",
      ),
    ).toBeNull();
    expect(
      within(screen.getByRole("dialog")).getByText("Serum Creatinine Level"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("dialog")).getByText("1.2"),
    ).toBeInTheDocument();
  });

  it("shows the stored unit on the synthetic input", async () => {
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(detailWith([row(), creatinine({ uunits: "mg/dL" })]));
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();
    await userEvent.click(
      screen.getByRole("button", {
        name: "Change what Estimated Glomerular Filtration Rate Minimum is worked out from",
      }),
    );
    expect(
      within(screen.getByRole("dialog")).getByText("mg/dL"),
    ).toBeInTheDocument();
  });

  it("seeds a descriptor-only input from the patient payload", async () => {
    const ageDescriptor: WritableFields = {
      patient_age: {
        kind: "computed",
        writable: false,
        inputs: ["date_of_birth"],
      },
      date_of_birth: {
        kind: "direct",
        writable: true,
        target: "patient_record",
        projection_target: "person",
        value_kind: "date",
      },
    };
    const ageRow = row({
      name: "ageMin",
      label: "Age Minimum",
      ufield: "patientAge",
      upatientField: "patient_age",
      uvalue: 68,
      utype: "int",
      value: 18,
    });
    const state = fakeState({ writable: ageDescriptor });
    const api = fakeApi();
    api.setDetail(detailWith([ageRow]));
    renderTrialMatches(api, {
      state: state.adapter,
      patientInfo: { date_of_birth: "1958-06-15" },
    });
    await openDetail();
    await userEvent.click(
      screen.getByRole("button", {
        name: "Change what Age Minimum is worked out from",
      }),
    );
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Edit Date Of Birth",
      }),
    );
    expect(
      await screen.findByLabelText("Date Of Birth"),
    ).toHaveValue("1958-06-15");
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
