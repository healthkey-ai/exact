// The explicit pre-existing-conditions answer, through the whole page (#5665).
//
// Two things have to be true and neither is about the checkbox: the row shows
// a control only when the HOST can answer (a descriptor entry alone is not
// enough), and the save carries the snapshot it read, because the endpoint
// refuses an answer whose starting point moved.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import { AnswerChanged } from "./state";
import type { ExplicitAnswerAdapter } from "./state";
import type { TrialDetailField } from "./types";
import type { WritableFields } from "./writable";

const ROW: TrialDetailField = {
  name: "preExistingConditionsExcluded",
  label: "Pre Existing Conditions Excluded",
  type: "multiselect",
  value: ["cardiacIssues"],
  ufield: "preExistingConditionsExcluded",
  upatientField: "pre_existing_condition_categories",
  uvalue: [],
  utype: "multiselect",
  matchingType: "unknown",
  ureadonly: false,
  upatientRecomputed: false,
};

const WRITABLE: WritableFields = {
  pre_existing_condition_categories: {
    kind: "explicit-answer",
    writable: false,
    endpoint: "preexisting-conditions-answer",
    value_kind: "string",
    multiple: true,
    answerable: true,
    options: [
      { value: "cardiacIssues", label: "Cardiac Issues" },
      { value: "renalImpairment", label: "Renal Impairment" },
    ],
  },
};

const detailWith = (fields: TrialDetailField[]) =>
  trialDetail(1, { details: { trialEligibilityAttributes: fields } } as never);

const ANSWER = {
  no_pre_existing_conditions: null as boolean | null,
  preexisting_conditions: ["renalImpairment"] as string[] | null,
};

function answers(overrides: Partial<ExplicitAnswerAdapter> = {}) {
  return {
    read: vi.fn(async () => ({ ...ANSWER })),
    write: vi.fn(async () => ({ ...ANSWER })),
    ...overrides,
  };
}

const open = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
};

function page(
  state: ReturnType<typeof fakeState>,
  overrides: Parameters<typeof renderTrialMatches>[1] = {},
) {
  const api = fakeApi();
  api.setDetail(detailWith([ROW]));
  renderTrialMatches(api, { state: state.adapter, ...overrides });
}

describe("the pre-existing-conditions answer", () => {
  it("offers a control only when the host can read and write the answer", async () => {
    page(fakeState({ writable: WRITABLE }));
    await open();

    // The descriptor says the field exists; without the adapter there is no
    // endpoint to ask, so the row must stay as it reads today.
    expect(screen.queryByRole("button", { name: /Answer Pre Existing/ })).toBeNull();
  });

  it("reads the answer when the dialog opens", async () => {
    const adapter = answers();
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { preExistingConditionsAnswer: adapter },
      }),
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer Pre Existing/ }),
    );

    expect(await screen.findByText(/This is your own answer/)).toBeInTheDocument();
    expect(adapter.read).toHaveBeenCalledTimes(1);
    // The recorded answer preselects "one or more" and the category.
    expect(screen.getByLabelText("Renal Impairment")).toBeChecked();
  });

  it("clears recorded categories before confirming 'none'", async () => {
    // The endpoint refuses to read recorded conditions as "none"; the
    // reviewed first step is the reader's explicit removal. Sending
    // `confirm_none` straight over the snapshot is a request the real
    // backend rejects, so the dialog must not make it.
    const adapter = answers();
    adapter.write = vi
      .fn()
      .mockResolvedValueOnce({
        no_pre_existing_conditions: null,
        preexisting_conditions: [],
      })
      .mockResolvedValueOnce({
        no_pre_existing_conditions: true,
        preexisting_conditions: null,
      });
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { preExistingConditionsAnswer: adapter },
      }),
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer Pre Existing/ }),
    );
    await userEvent.click(
      await screen.findByLabelText("I have none of these conditions"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(adapter.write).toHaveBeenCalledTimes(2));
    expect(adapter.write).toHaveBeenNthCalledWith(1, {
      action: "set_categories",
      categories: [],
      expected_flag: null,
      expected_categories: ["renalImpairment"],
    });
    expect(adapter.write).toHaveBeenNthCalledWith(2, {
      action: "confirm_none",
      expected_flag: null,
      expected_categories: [],
    });
  });

  it("treats an empty category selection as removing the answer", async () => {
    const adapter = answers();
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { preExistingConditionsAnswer: adapter },
      }),
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer Pre Existing/ }),
    );
    // Uncheck the recorded category; Save must remain available, because
    // this is the only way to undo it from this dialog.
    await userEvent.click(screen.getByLabelText("Renal Impairment"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(adapter.write).toHaveBeenCalled());
    expect(adapter.write).toHaveBeenCalledWith({
      action: "set_categories",
      categories: [],
      expected_flag: null,
      expected_categories: ["renalImpairment"],
    });
  });

  it("tells the host the new answer, so its inline profile is refreshed", async () => {
    const adapter = answers();
    const changed = vi.fn();
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { preExistingConditionsAnswer: adapter },
      }),
      { onPatientRecordChanged: changed },
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer Pre Existing/ }),
    );
    await userEvent.click(
      await screen.findByLabelText("I have one or more of these conditions"),
    );
    await userEvent.click(screen.getByLabelText("Cardiac Issues"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(changed).toHaveBeenCalledWith({
      no_pre_existing_conditions: null,
      preexisting_conditions: ["renalImpairment"],
    });
  });

  it("hides the control when the descriptor says this caller may not answer", async () => {
    // `writable: false` is about the PATCH; the endpoint has its own
    // permission and a read-only caller gets `answerable: false`.
    page(
      fakeState({
        writable: {
          pre_existing_condition_categories: {
            ...WRITABLE.pre_existing_condition_categories,
            answerable: false,
          },
        },
        overrides: { preExistingConditionsAnswer: answers() },
      }),
    );
    await open();

    expect(screen.queryByRole("button", { name: /Answer Pre Existing/ })).toBeNull();
  });

  it("sends the chosen categories in code space", async () => {
    const adapter = answers();
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { preExistingConditionsAnswer: adapter },
      }),
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer Pre Existing/ }),
    );
    await userEvent.click(
      await screen.findByLabelText("I have one or more of these conditions"),
    );
    await userEvent.click(screen.getByLabelText("Cardiac Issues"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(adapter.write).toHaveBeenCalled());
    expect(adapter.write).toHaveBeenCalledWith({
      action: "set_categories",
      categories: ["renalImpairment", "cardiacIssues"],
      expected_flag: null,
      expected_categories: ["renalImpairment"],
    });
  });

  it("says the answer moved, and offers to re-read it, on a 409", async () => {
    const adapter = answers({
      write: vi.fn(async () => {
        throw new AnswerChanged();
      }),
    });
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { preExistingConditionsAnswer: adapter },
      }),
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer Pre Existing/ }),
    );
    await userEvent.click(
      await screen.findByLabelText("I have none of these conditions"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/changed somewhere else/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload the answer" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reload the answer" }));
    await waitFor(() => expect(adapter.read).toHaveBeenCalledTimes(2));
  });
});
