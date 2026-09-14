// The edit control, through the whole page.
//
// Rendered by `TrialMatches` rather than in isolation, because what can go
// wrong here is not the input: it is a control appearing where PROMOP never
// said it could be written, or a save that does not reach the record, or an
// error shown for a write that succeeded. All three are about the wiring.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import type { WritableFields } from "./writable";
import type { TrialDetailField } from "./types";

const row = (over: Partial<TrialDetailField> = {}): TrialDetailField => ({
  name: "hemoglobin",
  label: "Hemoglobin",
  type: "number",
  value: 10,
  ufield: "hemoglobinLevel",
  upatientField: "hemoglobin_g_dl",
  uvalue: 12,
  utype: "number",
  units: "g/dL",
  matchingType: "matched",
  ...over,
});

const WRITABLE: WritableFields = {
  hemoglobin_g_dl: { kind: "direct", writable: true, value_kind: "number", unit: "g/dL" },
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

describe("when the control is drawn at all", () => {
  it("is not drawn for a host with no writer", async () => {
    // The default fake implements neither half of the pair, which is what a
    // host that has not opted into editing looks like.
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    renderTrialMatches(api, { state: fakeState().adapter });
    await openDetail();

    expect(await screen.findByText("Hemoglobin")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Hemoglobin" })).toBeNull();
  });

  it("is not drawn for a field PROMOP will not take a write to", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([row({ label: "BMI", upatientField: "bmi" })]));
    renderTrialMatches(api, {
      state: fakeState({
        writable: {
          bmi: {
            kind: "computed",
            writable: false,
            reason: "Derived from height and weight.",
          },
        },
      }).adapter,
    });
    await openDetail();

    expect(await screen.findByText("BMI")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit BMI" })).toBeNull();
  });

  it("is not drawn for a row EXACT could not name an attribute for", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([row({ label: "MIPI", upatientField: null })]));
    renderTrialMatches(api, { state: fakeState({ writable: WRITABLE }).adapter });
    await openDetail();

    expect(await screen.findByText("MIPI")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit MIPI" })).toBeNull();
  });

  it("is not drawn while the descriptor has not answered", async () => {
    // Never resolves. The page must read as it does today rather than
    // sprouting controls when the answer finally lands the other way.
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    const state = fakeState({ writable: WRITABLE });
    state.adapter.getWritableFields = vi.fn(() => new Promise<WritableFields>(() => {}));
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();

    expect(await screen.findByText("Hemoglobin")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Hemoglobin" })).toBeNull();
  });

  it("is not drawn when the descriptor could not be read", async () => {
    // A failed read leaves no answer, which is not the same as "nothing is
    // editable" — but it draws the same page, and must not draw controls.
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    const state = fakeState({ writable: WRITABLE });
    state.adapter.getWritableFields = vi.fn(async () => {
      throw new Error("nope");
    });
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();

    expect(await screen.findByText("Hemoglobin")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Hemoglobin" })).toBeNull();
  });

  it("is not drawn on a × upper-limit-of-normal row, whose number is a ratio", async () => {
    // Those rows inherit the base attribute's `ufield` verbatim, so PROMOP
    // answers "writable" for them. The number on screen is a RATIO: editing
    // "2.5" would write 2.5 into the absolute lab column. They are marked
    // `ureadonly`, and that is what withholds the control.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({ name: "hemoglobin_min", label: "Hemoglobin" }),
        row({ name: "hemoglobin_uln_min", label: "Hemoglobin ×ULN", ureadonly: true, uvalue: 2.5 }),
      ]),
    );
    renderTrialMatches(api, { state: fakeState({ writable: WRITABLE }).adapter });
    await openDetail();

    expect(await screen.findByText("Hemoglobin ×ULN")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Hemoglobin ×ULN" })).toBeNull();
    // The row that IS the attribute keeps its control.
    expect(screen.getByRole("button", { name: "Edit Hemoglobin" })).toBeInTheDocument();
  });

  it("draws one control per attribute, not one per row naming it", async () => {
    // A min and a max are two rows showing the same value of the same
    // attribute. Two pencils for one value invite the reader to wonder which
    // one is theirs.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({ name: "hemoglobin_min", label: "Hemoglobin (min)" }),
        row({ name: "hemoglobin_max", label: "Hemoglobin (max)" }),
      ]),
    );
    renderTrialMatches(api, { state: fakeState({ writable: WRITABLE }).adapter });
    await openDetail();

    await screen.findByText("Hemoglobin (min)");
    expect(screen.getAllByRole("button", { name: /^Edit Hemoglobin/ })).toHaveLength(1);
  });

  it("is not drawn for a value EXACT recomputes, however writable PROMOP says it is", async () => {
    // The two answer different questions. PROMOP says the record will TAKE
    // the write — and it does. EXACT then recomputes the value from its
    // inputs before the next match, so the re-read returns the old one: the
    // write is accepted, undone, and nothing anywhere reports an error
    // (#449). A box like that is worse than none.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({
          label: "Refractory status",
          upatientField: "treatment_refractory_status",
          upatientRecomputed: true,
        }),
      ]),
    );
    renderTrialMatches(api, {
      state: fakeState({
        writable: {
          treatment_refractory_status: {
            kind: "direct",
            writable: true,
            value_kind: "string",
            options: [{ value: "Refractory" }],
          },
        },
      }).adapter,
    });
    await openDetail();

    expect(await screen.findByText("Refractory status")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Refractory status" })).toBeNull();
  });

  it("is drawn for a field PROMOP says this caller may write", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    renderTrialMatches(api, { state: fakeState({ writable: WRITABLE }).adapter });
    await openDetail();

    expect(
      await screen.findByRole("button", { name: "Edit Hemoglobin" }),
    ).toBeInTheDocument();
  });
});

describe("saving", () => {
  const startEditing = async (state: ReturnType<typeof fakeState>, fields = [row()]) => {
    const api = fakeApi();
    api.setDetail(detailWith(fields));
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();
    await userEvent.click(
      await screen.findByRole("button", { name: `Edit ${fields[0].label}` }),
    );
    return api;
  };

  it("writes the value to the attribute PROMOP named, as a number", async () => {
    // Not "13" — the column is numeric, and a string would make the server
    // parse what the client already knows.
    const state = fakeState({ writable: WRITABLE });
    await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBe(13));
  });

  it("sends null for an emptied box, not an empty string", async () => {
    // The columns are nullable and "" is a value for many of them, so the
    // two are not interchangeable: one clears the field, the other fills it
    // with nothing.
    const state = fakeState({ writable: WRITABLE });
    await startEditing(state);

    await userEvent.clear(screen.getByRole("textbox", { name: "Hemoglobin" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBeNull());
  });

  it("writes an option's value, never the code beside it", async () => {
    const state = fakeState({
      writable: {
        breast_cancer_type: {
          kind: "direct",
          writable: true,
          value_kind: "string",
          options: [{ value: "Invasive ductal", code: "254837009" }],
        },
      },
    });
    await startEditing(state, [
      row({ label: "Type", upatientField: "breast_cancer_type", uvalue: null, units: undefined }),
    ]);

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Type" }),
      "Invasive ductal",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.record.breast_cancer_type).toBe("Invasive ductal"));
  });

  it("closes and leaves no error when the record canonicalised the value", async () => {
    // `differs` is not a failure: the record is re-derived from the OMOP fact
    // the write produced, and units come back canonicalised. Showing "could
    // not save" here would be a lie about a write that landed.
    const state = fakeState({ writable: WRITABLE });
    state.adapter.setPatientField = vi.fn(async () => ({
      status: "differs" as const,
      value: 12.5,
    }));
    await startEditing(state);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Save" })).toBeNull(),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the editor open, holding what was typed, when the write was refused", async () => {
    // A refused write that closed the box would make the reader type it again
    // to learn whether it was their value or the connection at fault.
    const state = fakeState({ writable: WRITABLE });
    state.adapter.setPatientField = vi.fn(async () => {
      throw new Error("403");
    });
    await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "9");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Hemoglobin" })).toHaveValue("9");
  });

  it("refuses a number that is not one, rather than erasing the field", async () => {
    // `type="number"` reports "" for anything it cannot parse, so a mistyped
    // "12..5" would have been indistinguishable from a cleared box — and a
    // cleared box sends null. A typo would have erased a lab value.
    const state = fakeState({ writable: WRITABLE });
    await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "12..5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a number");
    expect(state.adapter.setPatientField).not.toHaveBeenCalled();
    expect(box).toHaveValue("12..5");
  });

  it("re-reads the trial after a save, so the match recomputes", async () => {
    // The whole point of the phase: the value changes, the score follows. A
    // save that left the page showing the old status would look like nothing
    // happened.
    const state = fakeState({ writable: WRITABLE });
    const api = await startEditing(state);
    const before = api.detailRequests().length;

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.detailRequests().length).toBeGreaterThan(before));
  });

  it("stays on Saving… until the record has actually been re-read", async () => {
    // Resolving the save before the re-read lands would close the editor onto
    // the OLD value, and reopening would show it again — which reads as an
    // edit that did not take.
    const state = fakeState({ writable: WRITABLE });
    const api = await startEditing(state);
    const release = api.holdNextDetail();

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // The write itself is long done — the record already holds the value.
    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBe(13));
    expect(screen.getByRole("button", { name: "Saving…" })).toBeInTheDocument();

    release();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Saving|Save/ })).toBeNull(),
    );
  });

  it("opens a date box on a value the server sent as a full timestamp", async () => {
    // `<input type="date">` accepts only `YYYY-MM-DD`; anything else renders
    // EMPTY, and an empty box saved sends null. A reader who opened the
    // editor and saved without touching it would have erased the date.
    const state = fakeState({
      writable: { sct_date: { kind: "direct", writable: true, value_kind: "date" } },
    });
    await startEditing(state, [
      row({
        label: "Transplant date",
        upatientField: "sct_date",
        uvalue: "2024-01-05T00:00:00Z",
        units: undefined,
      }),
    ]);

    const box = screen.getByLabelText("Transplant date");
    expect(box).toHaveValue("2024-01-05");

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(state.record.sct_date).toBe("2024-01-05"));
  });

  it("shows a multiselect's existing markers, which arrive comma-joined", async () => {
    // The record stores them in one TextField and the serializer reads them
    // back joined, whatever the write sent. Seeded raw, the control would
    // show nothing selected and the reader would think the record was empty
    // — then save over it.
    const state = fakeState({
      writable: {
        cytogenetic_markers: {
          kind: "direct",
          writable: true,
          value_kind: "string",
          multiple: true,
          options: [
            { value: "del17p" },
            { value: "t(4;14)" },
            { value: "inv(3)(q21,q26)" },
          ],
        },
      },
    });
    await startEditing(state, [
      row({
        label: "Markers",
        upatientField: "cytogenetic_markers",
        // One marker carries a comma inside its brackets: split naively it
        // becomes two the record has never heard of.
        uvalue: "del17p, inv(3)(q21,q26)",
        units: undefined,
      }),
    ]);

    const box = screen.getByRole("listbox", { name: "Markers" });
    expect(box).toHaveValue(["del17p", "inv(3)(q21,q26)"]);
  });

  it("a boolean writes a boolean, not the word on the button", async () => {
    const state = fakeState({
      writable: { meets_crab: { kind: "direct", writable: true, value_kind: "boolean" } },
    });
    await startEditing(state, [
      row({ label: "CRAB", upatientField: "meets_crab", uvalue: false, units: undefined }),
    ]);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "CRAB" }), "true");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.record.meets_crab).toBe(true));
  });

  it("a multiselect writes the list, not one joined string", async () => {
    const state = fakeState({
      writable: {
        cytogenetic_markers: {
          kind: "direct", writable: true, value_kind: "string", multiple: true,
          options: [{ value: "del17p" }, { value: "t(4;14)" }],
        },
      },
    });
    await startEditing(state, [
      row({ label: "Markers", upatientField: "cytogenetic_markers", uvalue: "del17p", units: undefined }),
    ]);

    await userEvent.selectOptions(screen.getByRole("listbox", { name: "Markers" }), [
      "del17p",
      "t(4;14)",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(state.record.cytogenetic_markers).toEqual(["del17p", "t(4;14)"]),
    );
  });

  it("offers a value the option list has never heard of, rather than showing it as blank", async () => {
    // A legacy spelling selects nothing, so the box reads "—" while an
    // untouched Save sends the old value straight back: the screen says
    // cleared and the wire says unchanged.
    const state = fakeState({
      writable: {
        myeloma_type: {
          kind: "direct", writable: true, value_kind: "string",
          options: [{ value: "IgG" }, { value: "IgA" }],
        },
      },
    });
    await startEditing(state, [
      row({ label: "Type", upatientField: "myeloma_type", uvalue: "IgG kappa (legacy)", units: undefined }),
    ]);

    const box = screen.getByRole("combobox", { name: "Type" });
    expect(box).toHaveValue("IgG kappa (legacy)");
    expect(screen.getByRole("option", { name: /not in the list/ })).toBeInTheDocument();
  });

  it("refuses hex and exponent notation, which Number would have taken", async () => {
    const state = fakeState({ writable: WRITABLE });
    await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "0x10");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a number");
    expect(state.adapter.setPatientField).not.toHaveBeenCalled();
  });

  it("puts focus back on the pencil when the editor closes", async () => {
    // Dropped to <body>, a keyboard reader loses their place in a table of
    // fifty rows every time they cancel.
    const state = fakeState({ writable: WRITABLE });
    await startEditing(state);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Edit Hemoglobin" })).toHaveFocus(),
    );
  });

  it("saves on Enter and cancels on Escape from a select, not only from a text box", async () => {
    const state = fakeState({
      writable: {
        myeloma_type: {
          kind: "direct", writable: true, value_kind: "string",
          options: [{ value: "IgG" }, { value: "IgA" }],
        },
      },
    });
    await startEditing(state, [
      row({ label: "Type", upatientField: "myeloma_type", uvalue: "IgG", units: undefined }),
    ]);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Type" }), "IgA");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("combobox", { name: "Type" })).toBeNull();
    expect(state.adapter.setPatientField).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Edit Type" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Type" }), "IgA");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(state.record.myeloma_type).toBe("IgA"));
  });

  it("hides the old value while the editor stands in its place", async () => {
    const state = fakeState({ writable: WRITABLE });
    await startEditing(state);

    // "12" is the patient's value; only the box should be showing it now.
    expect(screen.queryByText("12", { selector: ".exact-elig__val" })).toBeNull();
  });

  it("does not wipe what the reader is typing when the record is re-read", async () => {
    // A save on another row re-reads the whole trial, so every open editor
    // sees a new `value`. Reseeding from it would erase a half-typed value
    // mid-keystroke — and with the queue in the next slice, re-reads stop
    // being something the reader initiated at all.
    const state = fakeState({
      writable: {
        hemoglobin_g_dl: { kind: "direct", writable: true, value_kind: "number" },
        platelet_count: { kind: "direct", writable: true, value_kind: "number" },
      },
    });
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({ name: "hgb", label: "Hemoglobin" }),
        row({ name: "plt", label: "Platelets", upatientField: "platelet_count", uvalue: 200 }),
      ]),
    );
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();

    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13.");

    // The server now reports something else for the row being typed into.
    api.setDetail(
      detailWith([
        row({ name: "hgb", label: "Hemoglobin", uvalue: 99 }),
        row({ name: "plt", label: "Platelets", upatientField: "platelet_count", uvalue: 200 }),
      ]),
    );
    await userEvent.click(screen.getByRole("button", { name: "Edit Platelets" }));
    // By the editor it belongs to: with two open there are two Save buttons,
    // and picking one by position saved the wrong field.
    const platelets = document.querySelector('[data-field="platelet_count"]') as HTMLElement;
    await userEvent.click(within(platelets).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(state.record.platelet_count).toBe(200));

    expect(screen.getByRole("textbox", { name: "Hemoglobin" })).toHaveValue("13.");
  });

  it("keeps a multiselect value the option list has never heard of", async () => {
    // Invisible, it would be deleted by the next change: the browser reports
    // only what is selected. PROMOP keeps such values on purpose — imported
    // text turns up in this field and its validator allows what is already
    // stored — so dropping them here would undo that deliberately.
    const state = fakeState({
      writable: {
        cytogenetic_markers: {
          kind: "direct", writable: true, value_kind: "string", multiple: true,
          options: [{ value: "del17p" }, { value: "t(4;14)" }],
        },
      },
    });
    await startEditing(state, [
      row({
        label: "Markers",
        upatientField: "cytogenetic_markers",
        uvalue: "del17p, some legacy text",
        units: undefined,
      }),
    ]);

    const box = screen.getByRole("listbox", { name: "Markers" });
    expect(box).toHaveValue(["del17p", "some legacy text"]);

    await userEvent.selectOptions(box, ["del17p", "t(4;14)", "some legacy text"]);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(state.record.cytogenetic_markers).toEqual([
        "del17p",
        "t(4;14)",
        "some legacy text",
      ]),
    );
  });

  it("cancels without writing anything", async () => {
    const state = fakeState({ writable: WRITABLE });
    await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "3");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("textbox", { name: "Hemoglobin" })).toBeNull();
    expect(state.adapter.setPatientField).not.toHaveBeenCalled();
  });
});
