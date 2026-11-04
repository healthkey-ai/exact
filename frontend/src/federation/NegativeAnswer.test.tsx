// The explicit negative confirmations, through the whole page (#5666).
//
// A descriptor entry alone is not a door: the host must supply the adapter,
// the row must be answerable for this caller, and the write must carry the
// value/source snapshot it read because the endpoint refuses a stale answer.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import { AnswerChanged } from "./state";
import type { NegativeAnswer, NegativeAnswerAdapter } from "./state";
import type { TrialDetailField } from "./types";
import type { WritableFields } from "./writable";

const ROW: TrialDetailField = {
  name: "noActiveInfectionRequired",
  label: "No Active Infection",
  type: "boolean",
  value: true,
  ufield: "noActiveInfectionRequired",
  upatientField: "no_active_infection_status",
  uvalue: null,
  utype: "boolean",
  matchingType: "unknown",
  ureadonly: false,
  upatientRecomputed: false,
};

const WRITABLE: WritableFields = {
  no_active_infection_status: {
    kind: "explicit-answer",
    writable: false,
    endpoint: "negative-answer",
    value_kind: "boolean",
    answerable: true,
    actions: ["confirm_negative", "withdraw"],
    action_labels: {
      confirm_negative: "Confirm no active infection",
      withdraw: "Withdraw confirmation",
    },
  },
};

const detailWith = (fields: TrialDetailField[]) =>
  trialDetail(1, { details: { trialEligibilityAttributes: fields } } as never);

const ANSWER: NegativeAnswer = {
  field: "no_active_infection_status",
  value: null,
  confirmed: false,
  source: null,
};

function answers(overrides: Partial<NegativeAnswerAdapter> = {}) {
  return {
    read: vi.fn(async () => ({ ...ANSWER })),
    write: vi.fn(async () => ({ ...ANSWER, value: true, confirmed: true })),
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

describe("the explicit negative confirmation", () => {
  it("offers a control only when the host supplies the negative adapter", async () => {
    page(fakeState({ writable: WRITABLE }));
    await open();
    expect(screen.queryByRole("button", { name: /Answer No Active Infection/ })).toBeNull();
  });

  it("reads the snapshot and confirms the negative", async () => {
    const adapter = answers();
    const changed = vi.fn();
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { negativeAnswers: adapter },
      }),
      { onPatientRecordChanged: changed },
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer No Active Infection/ }),
    );
    expect(await screen.findByText(/This is your own confirmation/)).toBeInTheDocument();
    expect(adapter.read).toHaveBeenCalledWith("no_active_infection_status");

    await userEvent.click(screen.getByRole("button", { name: "Confirm no active infection" }));
    await waitFor(() => expect(adapter.write).toHaveBeenCalledTimes(1));
    expect(adapter.write).toHaveBeenCalledWith({
      field: "no_active_infection_status",
      action: "confirm_negative",
      expected_value: null,
      expected_source: null,
    });
    await waitFor(() => expect(changed).toHaveBeenCalledWith({
      no_active_infection_status: true,
    }));
  });

  it("withdraws an existing confirmation", async () => {
    const adapter = answers({
      read: vi.fn(async () => ({
        ...ANSWER, value: true, confirmed: true,
      })),
      write: vi.fn(async () => ({
        ...ANSWER, value: null, confirmed: false,
      })),
    });
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { negativeAnswers: adapter },
      }),
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer No Active Infection/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Withdraw confirmation" }));
    await waitFor(() => expect(adapter.write).toHaveBeenCalledWith({
      field: "no_active_infection_status",
      action: "withdraw",
      expected_value: true,
      expected_source: null,
    }));
  });

  it("says the answer moved on a 409 and offers a reload", async () => {
    const adapter = answers({
      write: vi.fn(async () => {
        throw new AnswerChanged();
      }),
    });
    page(
      fakeState({
        writable: WRITABLE,
        overrides: { negativeAnswers: adapter },
      }),
    );
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer No Active Infection/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Confirm no active infection" }));
    expect(await screen.findByText(/Your answer changed somewhere else/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload the answer" })).toBeInTheDocument();
  });

  it("hides the control when the caller may not answer", async () => {
    page(
      fakeState({
        writable: {
          no_active_infection_status: {
            ...WRITABLE.no_active_infection_status,
            answerable: false,
          },
        },
        overrides: { negativeAnswers: answers() },
      }),
    );
    await open();
    expect(screen.queryByRole("button", { name: /Answer No Active Infection/ })).toBeNull();
  });
});
