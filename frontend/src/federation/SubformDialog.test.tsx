// The dialog, through the whole page.
//
// What can go wrong is not the markup: it is offering the door where there is
// nothing behind it, or writing an entry that EXACT derives anyway.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import type { WritableFields } from "./writable";
import type { SubformEntry, TrialDetailField } from "./types";

const entry = (over: Partial<SubformEntry> = {}): SubformEntry => ({
  name: "estrogenReceptorStatus",
  label: "Estrogen receptor status",
  type: "select",
  value: "er_minus",
  options: [
    { value: "er_minus", label: "ER-" },
    { value: "er_plus", label: "ER+" },
  ],
  upatientField: "estrogen_receptor_status",
  upatientRecomputed: false,
  ...over,
});

const composite = (over: Partial<TrialDetailField> = {}): TrialDetailField => ({
  name: "tnbcStatus",
  label: "TNBC Status",
  type: "boolean",
  value: true,
  // The row itself is computed and read-only — which is exactly why the
  // dialog exists.
  ureadonly: true,
  ufield: "tnbcStatus",
  upatientField: "tnbc_status",
  upatientRecomputed: true,
  uvalue: false,
  utype: "boolean",
  matchingType: "not_matched",
  subform_details: [entry()],
  ...over,
});

const WRITABLE: WritableFields = {
  estrogen_receptor_status: {
    kind: "direct",
    writable: true,
    value_kind: "string",
    options: [{ value: "ER-" }, { value: "ER+" }],
  },
};

function detailWith(fields: TrialDetailField[]) {
  return trialDetail(1, { details: { trialEligibilityAttributes: fields } } as never);
}

const open = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
};

describe("when the door is offered", () => {
  it("offers it on a computed row whose inputs can be written", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([composite()]));
    renderTrialMatches(api, { state: fakeState({ writable: WRITABLE }).adapter });
    await open();

    // No pencil on the row — EXACT recomputes it — and a way in all the same.
    expect(screen.queryByRole("button", { name: "Edit TNBC Status" })).toBeNull();
    expect(
      await screen.findByRole("button", { name: /Change what TNBC Status/ }),
    ).toBeInTheDocument();
  });

  it("does not offer it when every input is recomputed too", async () => {
    // The therapy groups: EXACT derives `first_line_therapy` and its siblings
    // as well, so the dialog would list four values and change none of them.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        composite({
          label: "Therapies",
          subform_details: [
            entry({
              name: "firstLineTherapy",
              label: "First line therapy",
              upatientField: "first_line_therapy",
              upatientRecomputed: true,
            }),
          ],
        }),
      ]),
    );
    renderTrialMatches(api, {
      state: fakeState({
        writable: { first_line_therapy: { kind: "direct", writable: true } },
      }).adapter,
    });
    await open();

    expect(await screen.findByText("Therapies")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Change what/ })).toBeNull();
  });

  it("does not offer it when PROMOP will not take the inputs either", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([composite()]));
    renderTrialMatches(api, {
      state: fakeState({
        writable: {
          estrogen_receptor_status: {
            kind: "computed",
            writable: false,
            reason: "Derived.",
          },
        },
      }).adapter,
    });
    await open();

    expect(await screen.findByText("TNBC Status")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Change what/ })).toBeNull();
  });

  it("does not offer it to a host with no writer at all", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([composite()]));
    renderTrialMatches(api, { state: fakeState().adapter });
    await open();

    expect(await screen.findByText("TNBC Status")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Change what/ })).toBeNull();
  });
});

describe("inside the dialog", () => {
  const openDialog = async () => {
    const api = fakeApi();
    api.setDetail(detailWith([composite()]));
    const state = fakeState({ writable: WRITABLE });
    renderTrialMatches(api, { state: state.adapter });
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Change what TNBC Status/ }),
    );
    return state;
  };

  it("writes an input through the same queue the rows use", async () => {
    const state = await openDialog();
    const dialog = screen.getByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Edit Estrogen receptor status" }),
    );
    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "Estrogen receptor status" }),
      "ER+",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.record.estrogen_receptor_status).toBe("ER+"));
  });

  it("takes focus and closes on Escape", async () => {
    // Without it a keyboard reader tabs through the whole page behind the
    // dialog to reach its contents.
    await openDialog();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps Tab inside the dialog", async () => {
    // `aria-modal` tells a screen reader the rest of the page is inert and
    // does nothing to the Tab key. Without a trap the reader tabs out into
    // controls they cannot see.
    await openDialog();
    const dialog = screen.getByRole("dialog");

    for (let i = 0; i < 8; i += 1) {
      await userEvent.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
    await userEvent.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it("puts focus back on the button that opened it", async () => {
    // Otherwise the reader lands at the top of a long table with no idea
    // where they were.
    await openDialog();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      screen.getByRole("button", { name: /Change what TNBC Status/ }),
    ).toHaveFocus();
  });

  it("stays open when the reader clicks inside it", async () => {
    // The scrim closes on click, and the panel sits on the scrim.
    await openDialog();
    await userEvent.click(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("draws no control on an input EXACT recomputes, even beside one it does not", async () => {
    // The door opens because the receptor status can be written; the therapy
    // line beside it cannot, and PROMOP would take that write happily. Only
    // EXACT knows it does not survive.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        composite({
          subform_details: [
            entry(),
            entry({
              name: "firstLineTherapy",
              label: "First line therapy",
              value: "VRd",
              upatientField: "first_line_therapy",
              upatientRecomputed: true,
            }),
          ],
        }),
      ]),
    );
    renderTrialMatches(api, {
      state: fakeState({
        writable: {
          ...WRITABLE,
          first_line_therapy: { kind: "direct", writable: true, value_kind: "string" },
        },
      }).adapter,
    });
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Change what TNBC Status/ }),
    );

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: "Edit Estrogen receptor status" }),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("First line therapy")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Edit First line therapy" }),
    ).toBeNull();
  });

  it("shows an entry that cannot be written, without a control", async () => {
    // Listing it is the point: the reader is being told what the row is made
    // of, and a value they cannot change is still part of the answer.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        composite({
          subform_details: [
            entry(),
            entry({
              name: "her2Status",
              label: "HER2 status",
              value: "her2_low",
              upatientField: "her2_status",
            }),
          ],
        }),
      ]),
    );
    renderTrialMatches(api, { state: fakeState({ writable: WRITABLE }).adapter });
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Change what TNBC Status/ }),
    );

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("HER2 status")).toBeInTheDocument();
    expect(within(dialog).getByText("her2_low")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Edit HER2 status" }),
    ).toBeNull();
  });
});
