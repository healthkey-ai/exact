/**
 * A boolean trial-side value can sit over a list-valued patient field.
 *
 * `stem_cell_transplant_history` is a list of SCT types on the patient, while
 * the trial asks a boolean "has any transplant history". The row's `uvalue` is
 * that boolean; seeding a multiselect from it adds `true` as an option the
 * record has never heard of and loses the actual choice.
 */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import type { WritableFields } from "./writable";
import type { TrialDetailField } from "./types";

const WRITABLE: WritableFields = {
  stem_cell_transplant_history: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "string",
    multiple: true,
    overridable: true,
    options: [
      { value: "allogeneic SCT" },
      { value: "autologous SCT" },
      { value: "tandem SCT" },
    ],
  },
};

const row: TrialDetailField = {
  name: "stemCellTransplantHistoryRequired",
  label: "Stem Cell Transplant History",
  type: "boolean",
  value: false,
  ufield: "stemCellTransplantHistory",
  upatientField: "stem_cell_transplant_history",
  ureadonly: false,
  upatientRecomputed: true,
  uvalue: true,
  utype: "select",
  uoptions: [
    { value: "", label: "Unknown" },
    { value: "completedASCT", label: "Completed ASCT" },
  ],
  matchingType: "matched",
};

const detailWith = (fields: TrialDetailField[]) =>
  trialDetail(1, { details: { trialEligibilityAttributes: fields } } as never);

const openDetail = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
};

describe("a boolean trial value over a list patient field", () => {
  it("seeds the editor from the patient list, not from `true`", async () => {
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(detailWith([row]));
    renderTrialMatches(api, {
      state: state.adapter,
      patientInfo: { stem_cell_transplant_history: ["autologous SCT"] },
    });
    await openDetail();

    await userEvent.click(
      screen.getByRole("button", { name: "Edit Stem Cell Transplant History" }),
    );

    const list = screen.getByRole("listbox", { name: "Stem Cell Transplant History" });
    expect(
      [...(list as HTMLSelectElement).selectedOptions].map((option) => option.value),
    ).toEqual(["autologous SCT"]);
    expect(within(list).queryByText(/not in the list/)).toBeNull();
  });

  it("keeps a control on the excluded row, which shows a different answer", async () => {
    // Both rows name `stem_cell_transplant_history`. The required row reads the
    // boolean ("has any transplant"), the excluded row the list itself. They
    // answer different questions, so the second row must not lose its pencil to
    // the one-control-per-attribute rule that merges a min/max pair.
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(detailWith([
      row,
      {
        ...row,
        name: "stemCellTransplantHistoryExcluded",
        label: "Stem Cell Transplant History Excluded",
        type: "multiselect",
        value: ["priorAllogeneicSCT"],
        utype: "select",
        uvalue: ["autologous SCT"],
      },
    ]));
    renderTrialMatches(api, {
      state: state.adapter,
      patientInfo: { stem_cell_transplant_history: ["autologous SCT"] },
    });
    await openDetail();

    expect(
      screen.getByRole("button", { name: "Edit Stem Cell Transplant History" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Edit Stem Cell Transplant History Excluded",
      }),
    ).toBeInTheDocument();
  });
});
