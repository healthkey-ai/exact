// The dialog, through the whole page.
//
// What can go wrong is not the markup: it is offering the door where there is
// nothing behind it, or writing an entry that EXACT derives anyway.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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

  it("does not offer it when the only writable inputs are demographics", async () => {
    // Found by /qa on 2026-09-14. Six ULN groups are [the lab, gender,
    // ethnicity]: the lab is an `alias` in PROMOP and not writable, while
    // gender and ethnicity are — so the door opened onto a dialog captioned
    // "the values it is computed from" whose only two controls change the
    // patient's demographics, record-wide, from a threshold row.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        composite({
          label: "ALT ×ULN",
          upatientField: "liver_enzyme_level_alt_uln_min",
          subform_details: [
            entry({
              name: "liverEnzymeLevelsAlt",
              label: "ALT",
              value: 75,
              upatientField: "liver_enzyme_levels_alt",
            }),
            entry({ name: "gender", label: "Gender", value: "M", upatientField: "gender" }),
            entry({
              name: "ethnicity",
              label: "Ethnicity",
              value: "caucasian_or_european",
              upatientField: "ethnicity",
            }),
          ],
        }),
      ]),
    );
    renderTrialMatches(api, {
      state: fakeState({
        writable: {
          // What PROMOP really says about these three.
          liver_enzyme_levels_alt: {
            kind: "alias", writable: false, canonical: "alt_u_l",
            reason: "Mirrors alt_u_l; edit that field instead.",
          },
          gender: {
            kind: "direct", writable: true, value_kind: "string",
            projection_target: "person", options: [{ value: "M" }, { value: "F" }],
          },
          ethnicity: {
            kind: "direct", writable: true, value_kind: "string",
            projection_target: "person",
          },
        },
      }).adapter,
    });
    await open();

    expect(await screen.findByText("ALT ×ULN")).toBeInTheDocument();
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

  it("does not close when Escape cancels a field editor inside it", async () => {
    // Found by /qa on 2026-09-14. The dialog listens on the document, and the
    // editor's Escape did not stop propagating — so one keystroke discarded
    // the draft AND shut the dialog around it.
    await openDialog();
    const dialog = screen.getByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Edit Estrogen receptor status" }),
    );
    await userEvent.keyboard("{Escape}");

    expect(
      within(dialog).queryByRole("combobox", { name: "Estrogen receptor status" }),
    ).toBeNull();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows a value the way the row does, not as a raw code", async () => {
    // Found by /qa on 2026-09-14: the dialog printed `String(value)`, so a
    // code appeared where the row shows a label, `false` where it shows "No",
    // and "[object Object]" for the JSON entries.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        composite({
          subform_details: [
            entry({
              value: "er_minus",
              options: [
                { value: "er_minus", label: "ER-" },
                { value: "er_plus", label: "ER+" },
              ],
            }),
            entry({
              name: "boneOnly",
              label: "Bone only",
              type: "boolean",
              value: false,
              options: null,
              upatientField: "bone_only_metastasis_status",
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
    expect(within(dialog).getByText("ER-")).toBeInTheDocument();
    expect(within(dialog).queryByText("er_minus")).toBeNull();
    expect(within(dialog).getByText("No")).toBeInTheDocument();
  });

  it("shows the unit the patient's value is stored in", async () => {
    // Found by /qa on 2026-09-14: the subform carried no unit at all, so a
    // lab was shown and typed bare — and CRAB converts through the stored
    // unit, so the wrong scale flips the composite silently.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        composite({
          subform_details: [
            entry({
              name: "serumCalciumLevel",
              label: "Serum calcium",
              type: "number",
              value: 10.4,
              options: null,
              upatientField: "serum_calcium_level",
              uunits: "mg/dL",
            }),
          ],
        }),
      ]),
    );
    renderTrialMatches(api, {
      state: fakeState({
        writable: {
          serum_calcium_level: { kind: "direct", writable: true, value_kind: "number" },
        },
      }).adapter,
    });
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Change what TNBC Status/ }),
    );

    expect(within(screen.getByRole("dialog")).getByText("mg/dL")).toBeInTheDocument();
  });

  it("says a subform write was refused, even after the dialog is closed", async () => {
    // A row paints only its own attribute's failure, and a subform input is by
    // construction a different one — so a refusal there had nowhere to appear
    // once the dialog was shut. A write that vanishes with nothing said is
    // exactly what this phase exists to prevent.
    const state = fakeState({ writable: WRITABLE });
    state.adapter.setPatientFields = vi.fn(async () => {
      throw new Error("400");
    });
    const api = fakeApi();
    api.setDetail(detailWith([composite()]));
    renderTrialMatches(api, { state: state.adapter });
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Change what TNBC Status/ }),
    );
    const dialog = screen.getByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Edit Estrogen receptor status" }),
    );
    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "Estrogen receptor status" }),
      "ER+",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    // Closed before the answer comes back, which is the whole difficulty.
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    const notice = await screen.findByRole("alert");
    // Named the way the reader saw it, not `estrogen_receptor_status`.
    expect(notice).toHaveTextContent("Estrogen receptor status");
    expect(notice).toHaveTextContent("could not be saved");
  });

  it("does not repeat a refusal the row already shows", async () => {
    // The row that carries the control paints its own failure in place, so the
    // notice above the table must stay out of it: two alerts about one
    // refusal is worse than one.
    const state = fakeState({
      writable: { hemoglobin_g_dl: { kind: "direct", writable: true, value_kind: "number" } },
    });
    state.adapter.setPatientFields = vi.fn(async () => {
      throw new Error("400");
    });
    const api = fakeApi();
    api.setDetail(
      detailWith([
        {
          name: "hemoglobinMin",
          label: "Hemoglobin",
          type: "number",
          value: 10,
          ufield: "hemoglobinLevel",
          upatientField: "hemoglobin_g_dl",
          upatientRecomputed: false,
          uvalue: 11.2,
          matchingType: "matched",
        },
      ]),
    );
    renderTrialMatches(api, { state: state.adapter });
    await open();

    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    const alerts = await screen.findAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent("Couldn't save that");
  });

  it("names every refused field, not just the first", async () => {
    const state = fakeState({
      writable: {
        ...WRITABLE,
        progesterone_receptor_status: {
          kind: "direct", writable: true, value_kind: "string",
          options: [{ value: "PR-" }, { value: "PR+" }],
        },
      },
    });
    state.adapter.setPatientFields = vi.fn(async () => {
      throw new Error("400");
    });
    const api = fakeApi();
    api.setDetail(
      detailWith([
        composite({
          subform_details: [
            entry(),
            entry({
              name: "progesteroneReceptorStatus",
              label: "Progesterone receptor status",
              value: "PR-",
              options: [
                { value: "PR-", label: "PR-" },
                { value: "PR+", label: "PR+" },
              ],
              upatientField: "progesterone_receptor_status",
            }),
          ],
        }),
      ]),
    );
    renderTrialMatches(api, { state: state.adapter });
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Change what TNBC Status/ }),
    );
    const dialog = screen.getByRole("dialog");
    for (const label of ["Estrogen receptor status", "Progesterone receptor status"]) {
      await userEvent.click(within(dialog).getByRole("button", { name: `Edit ${label}` }));
      await userEvent.selectOptions(
        within(dialog).getByRole("combobox", { name: label }),
        label.startsWith("Estrogen") ? "ER+" : "PR+",
      );
      await userEvent.click(
        within(dialog).getAllByRole("button", { name: "Save" })[0],
      );
    }
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("Estrogen receptor status");
    expect(notice).toHaveTextContent("Progesterone receptor status");
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
