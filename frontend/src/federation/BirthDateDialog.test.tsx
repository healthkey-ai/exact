// The age dialog saves the birth date and its precision as one answer (#5664).
//
// The generic subform saves one entry per Save. On a year-only record that
// truncates a full date to January 1st against the old precision, and a later
// "day" save promotes the placeholder to a complete date. Both values therefore
// leave in the same tick, which the field queue merges into one PATCH.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import type { SubformEntry, TrialDetailField } from "./types";
import type { WritableFields } from "./writable";

const entry = (over: Partial<SubformEntry>): SubformEntry => ({
  name: "dateOfBirth",
  label: "Date Of Birth",
  type: "date",
  value: null,
  upatientField: "date_of_birth",
  upatientRecomputed: false,
  ...over,
});

const ROW: TrialDetailField = {
  name: "ageLowLimit",
  label: "Age Minimum",
  type: "int",
  value: 18,
  ureadonly: true,
  ufield: "patientAge",
  upatientField: "patient_age",
  upatientRecomputed: false,
  uvalue: 68,
  utype: "int",
  matchingType: "unknown",
  subform_details: [
    entry({ value: "1980-05-20" }),
    entry({
      name: "dateOfBirthPrecision",
      label: "Date Of Birth Precision",
      type: "string",
      value: "year",
      upatientField: "date_of_birth_precision",
    }),
  ],
};

const WRITABLE: WritableFields = {
  date_of_birth: { kind: "direct", writable: true, value_kind: "date" },
  date_of_birth_precision: {
    kind: "direct",
    writable: true,
    value_kind: "string",
    options: [
      { value: "year", label: "Year only" },
      { value: "month", label: "Month and year" },
      { value: "day", label: "Full date" },
    ],
  },
};

const open = async () => {
  const api = fakeApi();
  api.setDetail(trialDetail(1, {
    details: { trialEligibilityAttributes: [ROW] },
  } as never));
  const state = fakeState({ writable: WRITABLE });
  renderTrialMatches(api, { state: state.adapter });
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
  await userEvent.click(
    await screen.findByRole("button", { name: /Change what Age Minimum/ }),
  );
  return state;
};

describe("the age row's birth date", () => {
  it("shows the recorded date and precision together", async () => {
    await open();

    expect(await screen.findByLabelText("Date of birth")).toHaveValue("1980-05-20");
    expect(screen.getByLabelText("Date of birth precision")).toHaveValue("year");
  });

  it("saves the date and the precision in one request", async () => {
    const state = await open();
    const date = screen.getByLabelText("Date of birth");
    await userEvent.clear(date);
    await userEvent.type(date, "1980-12-20");
    await userEvent.selectOptions(
      screen.getByLabelText("Date of birth precision"),
      "day",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.adapter.setPatientFields).toHaveBeenCalled());
    // One call, both fields: the queue merged them, so PROMOP cannot truncate
    // the date against the old precision before the new one arrives.
    expect(state.adapter.setPatientFields).toHaveBeenCalledTimes(1);
    expect(state.adapter.setPatientFields).toHaveBeenCalledWith({
      date_of_birth: "1980-12-20",
      date_of_birth_precision: "day",
    });
  });
});
