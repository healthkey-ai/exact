// The edit control, through the whole page.
//
// Rendered by `TrialMatches` rather than in isolation, because what can go
// wrong here is not the input: it is a control appearing where PROMOP never
// said it could be written, or a save that does not reach the record, or an
// error shown for a write that succeeded. All three are about the wiring.

import { StrictMode } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TrialMatches } from "./TrialMatches";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeApi, fakeState, renderTrialMatches, trialDetail } from "../test/renderTrialMatches";
import type { WriteOutcome } from "./state";
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
    state.adapter.setPatientFields = vi.fn(async (fields: Record<string, unknown>) =>
      Object.fromEntries(
        Object.keys(fields).map((f) => [f, { status: "differs" as const, value: 12.5 }]),
      ),
    );
    await startEditing(state);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Save" })).toBeNull(),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so on the row when the write was refused, and stops showing the value", async () => {
    // With a queue the editor is long closed by the time an answer arrives,
    // so the row is where this has to be said. And the optimistic value goes:
    // leaving it up would show a value the record does not hold.
    const state = fakeState({ writable: WRITABLE });
    state.adapter.setPatientFields = vi.fn(async () => {
      throw new Error("403");
    });
    await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "9");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save that");
    expect(screen.queryByRole("textbox", { name: "Hemoglobin" })).toBeNull();
    expect(screen.queryByText("Saving…")).toBeNull();
    // The row tells the truth — the record still holds 12 — and the reader's
    // own value is not lost: the editor opens on it, so retrying is a click
    // rather than remembering what they typed. PROMOP validates a PATCH as
    // one transaction, so a single bad field can refuse a whole batch, and
    // retyping three good values because of a fourth is not a thing to ask.
    expect(screen.getByText("12")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit Hemoglobin" }));
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
    // Past the debounce, not before it: checked immediately this would pass
    // whether or not the value had been queued.
    await new Promise((r) => setTimeout(r, 400));
    expect(state.adapter.setPatientFields).not.toHaveBeenCalled();
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

  it("shows the reader's own value, and says it is saving, until the write comes back", async () => {
    // The record does not hold it yet. Showing the old value for as long as
    // that takes reads as a save that did not take — and the reader has no
    // way to tell the difference from one that failed.
    const state = fakeState({ writable: WRITABLE });
    let release: (() => void) | null = null;
    state.adapter.setPatientFields = vi.fn(
      (fields: Record<string, unknown>) =>
        new Promise<Record<string, WriteOutcome>>((resolve) => {
          release = () =>
            resolve(
              Object.fromEntries(
                Object.entries(fields).map(([f, v]) => [f, { status: "saved", value: v }]),
              ),
            );
        }),
    );
    await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // Closed at once, and the row carries the value and the word.
    expect(screen.queryByRole("textbox", { name: "Hemoglobin" })).toBeNull();
    expect(await screen.findByText("Saving…")).toBeInTheDocument();
    expect(screen.getByText("13")).toBeInTheDocument();

    // The request itself has not gone out yet — "Saving…" is the row's own
    // promise, made the moment the reader pressed Save, not a report of a
    // request in flight.
    await waitFor(() => expect(release).not.toBeNull());
    release!();
    await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
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

  it("draws a multiselect from the row when the descriptor has no list", async () => {
    // FLIPI. PROMOP's descriptor says only "writable, string": the value set
    // lives in EXACT (`trials/services/value_options.py`) and rides on the
    // row. Asking the descriptor alone drew a free text box over a column the
    // matcher reads as a list of codes.
    const state = fakeState({
      writable: {
        flipi_score_options: { kind: "direct", writable: true, value_kind: "string" },
      },
    });
    await startEditing(state, [
      row({
        label: "FLIPI",
        upatientField: "flipi_score_options",
        uvalue: "age",
        utype: "multiselect",
        uoptions: [
          { value: "age", label: "Age over 60" },
          { value: "stage", label: "Ann Arbor III or IV" },
        ],
        units: undefined,
      }),
    ]);

    await userEvent.selectOptions(screen.getByRole("listbox", { name: "FLIPI" }), [
      "age",
      "stage",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // Joined, not a list. Measured against PROMOP: `PatientRecordSerializer`
    // generates a CharField here and answers a JSON array with "Not a valid
    // string." before `validate_flipi_score_options` ever runs.
    await waitFor(() => expect(state.record.flipi_score_options).toBe("age,stage"));
  });

  it("clears the field when every option is deselected", async () => {
    // Not "": PROMOP can tell an assessment of zero factors from no
    // assessment, but EXACT cannot — `scope_by_options` returns None for
    // zero, `is_attr_blank` calls "" blank, and the row reads "—" either way.
    // An emptied control means no answer here, as it does for every other
    // control in this editor.
    const state = fakeState({
      writable: {
        flipi_score_options: { kind: "direct", writable: true, value_kind: "string" },
      },
    });
    await startEditing(state, [
      row({
        label: "FLIPI",
        upatientField: "flipi_score_options",
        uvalue: "age",
        utype: "multiselect",
        uoptions: [{ value: "age", label: "Age over 60" }],
        units: undefined,
      }),
    ]);

    await userEvent.deselectOptions(screen.getByRole("listbox", { name: "FLIPI" }), ["age"]);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.record.flipi_score_options).toBeNull());
  });

  it("leaves an already-empty selection exactly as the record holds it", async () => {
    // "" is a real answer for FLIPI — an assessment that found no factors,
    // `flipi_score` 0, category "Low". It renders as nothing selected, which
    // is also what no assessment renders as. An untouched Save must not turn
    // one into the other: nobody pressed anything.
    const state = fakeState({
      record: { flipi_score_options: "" },
      writable: {
        flipi_score_options: { kind: "direct", writable: true, value_kind: "string" },
      },
    });
    await startEditing(state, [
      row({
        label: "FLIPI",
        upatientField: "flipi_score_options",
        uvalue: "",
        utype: "multiselect",
        uoptions: [{ value: "age", label: "Age over 60" }],
        units: undefined,
      }),
    ]);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // Asserted on the payload, not on the record: a rule that wrongly sent
    // null would leave the record reading "" for a while too, so a test that
    // only looked there would pass without the rule.
    await waitFor(() =>
      expect(state.adapter.setPatientFields).toHaveBeenCalledWith({
        flipi_score_options: "",
      }),
    );
  });

  it("takes back a refused edit without destroying what the record holds", async () => {
    // The gesture: the reader's save was refused, they reopen and deselect
    // their own attempt — "never mind". The record still holds "", a real
    // FLIPI assessment of zero factors. Nothing about that changed, so
    // nothing about it may be written.
    //
    // This is the one finding the property harness cannot see: it is about
    // WHICH value the editor compares against, not about what `payloadFrom`
    // does with it. The box shows the refused value on purpose; the decision
    // to save must not.
    const state = fakeState({
      record: { flipi_score_options: "" },
      writable: {
        flipi_score_options: { kind: "direct", writable: true, value_kind: "string" },
      },
    });
    const sent: unknown[] = [];
    state.adapter.setPatientFields = vi.fn(async (fields: Record<string, unknown>) => {
      sent.push(fields);
      throw new Error("403");
    });
    await startEditing(state, [
      row({
        label: "FLIPI",
        upatientField: "flipi_score_options",
        uvalue: "",
        utype: "multiselect",
        uoptions: [
          { value: "age", label: "Age over 60" },
          { value: "stage", label: "Ann Arbor III or IV" },
        ],
        units: undefined,
      }),
    ]);

    await userEvent.selectOptions(screen.getByRole("listbox", { name: "FLIPI" }), ["age"]);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save that");

    // Reopen: the box carries their refused "age" back, by design.
    await userEvent.click(screen.getByRole("button", { name: "Edit FLIPI" }));
    await userEvent.deselectOptions(screen.getByRole("listbox", { name: "FLIPI" }), ["age"]);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]).toEqual({ flipi_score_options: "" });
  });

  it("keeps the number box for a numeric column that has a row vocabulary", async () => {
    // Five attributes are `value_kind: "number"` in the descriptor and carry
    // an option list on the row (`ecog_performance_status` among them). The
    // row's list does not get to turn those into a select: the box sends 3,
    // a select would send "3", and that is a different change from this one.
    const state = fakeState({
      writable: {
        ecog_performance_status: { kind: "direct", writable: true, value_kind: "number" },
      },
    });
    await startEditing(state, [
      row({
        label: "ECOG",
        upatientField: "ecog_performance_status",
        uvalue: 1,
        utype: "select",
        uoptions: [
          { value: 0, label: "0 — Fully active" },
          { value: 2, label: "2 — Ambulatory" },
        ],
        units: undefined,
      }),
    ]);

    const box = screen.getByRole("textbox", { name: "ECOG" });
    await userEvent.clear(box);
    await userEvent.type(box, "2");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.record.ecog_performance_status).toBe(2));
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
    await new Promise((r) => setTimeout(r, 400));
    expect(state.adapter.setPatientFields).not.toHaveBeenCalled();
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
    await new Promise((r) => setTimeout(r, 400));
    expect(state.adapter.setPatientFields).not.toHaveBeenCalled();

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

  it("sends two fields filled in one breath as one request", async () => {
    // The point of the queue. Every write re-derives the projection and
    // rescores the match, so two gaps filled in a row should cost one of
    // each — not two, with the reader watching the list reshuffle twice.
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
    const hgb = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(hgb);
    await userEvent.type(hgb, "13");
    await userEvent.click(
      within(document.querySelector('[data-field="hemoglobin_g_dl"]') as HTMLElement)
        .getByRole("button", { name: "Save" }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Edit Platelets" }));
    const plt = screen.getByRole("textbox", { name: "Platelets" });
    await userEvent.clear(plt);
    await userEvent.type(plt, "250");
    await userEvent.click(
      within(document.querySelector('[data-field="platelet_count"]') as HTMLElement)
        .getByRole("button", { name: "Save" }),
    );

    await waitFor(() => expect(state.record.platelet_count).toBe(250));
    expect(state.record.hemoglobin_g_dl).toBe(13);
    expect(state.adapter.setPatientFields).toHaveBeenCalledTimes(1);
    expect((state.adapter.setPatientFields as ReturnType<typeof vi.fn>).mock.calls[0][0])
      .toEqual({ hemoglobin_g_dl: 13, platelet_count: 250 });
  });

  it("...and does the same when the payload names a patient of its own", async () => {
    // The same switch, with an id INSIDE the payload — ht-phr's shape. The
    // two props identify different things: the payload is what the server
    // answers from, `personId` is what the host's write adapter PATCHes. A
    // handle taken from the payload alone holds still here, the writer built
    // for Alice is not rebuilt, and its `live()` resolves against Bob's
    // adapter: Alice's haemoglobin is written into Bob's record.
    const writable: WritableFields = {
      hemoglobin_g_dl: { kind: "direct", writable: true, value_kind: "number" },
    };
    const alice = fakeState({ writable });
    const bob = fakeState({ writable });
    const api = fakeApi();
    api.setDetail(detailWith([row()]));

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    // One payload object, shared: the host moved only `personId`.
    const payload = { person_id: 9009, disease: "multiple myeloma" };
    const ui = (personId: string, state: typeof alice.adapter) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={payload}
          personId={personId}
          state={state}
        />
      </QueryClientProvider>
    );
    const view = render(ui("alice", alice.adapter));

    await userEvent.click(
      (await screen.findAllByRole("button", { name: "View Trial" }))[0],
    );
    await screen.findByText("Back to all trials");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // Still inside the debounce.
    view.rerender(ui("bob", bob.adapter));

    await waitFor(() => expect(alice.record.hemoglobin_g_dl).toBe(13));
    expect(bob.record).toEqual({});
    expect(bob.adapter.setPatientFields).not.toHaveBeenCalled();
  });

  it("...and when a blank id sits in front of the real one", async () => {
    // `person_id: ""` is a real shape — a column that exists and is empty —
    // and it is checked before `id`. Taken as an id on position alone, every
    // patient carrying it hashes to the same handle, so the writer built for
    // one is never rebuilt and `live()` sends the edit through the next
    // one's adapter.
    const writable: WritableFields = {
      hemoglobin_g_dl: { kind: "direct", writable: true, value_kind: "number" },
    };
    const alice = fakeState({ writable });
    const bob = fakeState({ writable });
    const api = fakeApi();
    api.setDetail(detailWith([row()]));

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (id: number, state: typeof alice.adapter) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ person_id: "", id, disease: "multiple myeloma" }}
          state={state}
        />
      </QueryClientProvider>
    );
    const view = render(ui(9009, alice.adapter));

    await userEvent.click(
      (await screen.findAllByRole("button", { name: "View Trial" }))[0],
    );
    await screen.findByText("Back to all trials");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // Still inside the debounce.
    view.rerender(ui(9010, bob.adapter));

    await waitFor(() => expect(alice.record.hemoglobin_g_dl).toBe(13));
    expect(bob.record).toEqual({});
    expect(bob.adapter.setPatientFields).not.toHaveBeenCalled();
  });

  it("writes a queued edit into the patient it was made for, not the next one", async () => {
    // The writer's flush runs on a patient switch, and the refs it would
    // reach through are written during render — so by then they describe the
    // NEW patient. For a saved search that is an annoyance; for a haemoglobin
    // it is one person's lab value in another person's chart.
    const writable: WritableFields = {
      hemoglobin_g_dl: { kind: "direct", writable: true, value_kind: "number" },
    };
    const alice = fakeState({ writable });
    const bob = fakeState({ writable });
    const api = fakeApi();
    api.setDetail(detailWith([row()]));

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (personId: string, state: typeof alice.adapter) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma" }}
          personId={personId}
          state={state}
        />
      </QueryClientProvider>
    );
    const view = render(ui("alice", alice.adapter));

    await userEvent.click(
      (await screen.findAllByRole("button", { name: "View Trial" }))[0],
    );
    await screen.findByText("Back to all trials");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // Still inside the debounce: the edit is queued, nothing is on the wire.
    view.rerender(ui("bob", bob.adapter));
    await waitFor(() => expect(alice.record.hemoglobin_g_dl).toBe(13));
    expect(bob.record).toEqual({});
    expect(bob.adapter.setPatientFields).not.toHaveBeenCalled();

    // And the other direction: the queue belongs to the patient, so Bob's own
    // edit must not go out through the adapter it captured for Alice.
    await userEvent.click(
      (await screen.findAllByRole("button", { name: "View Trial" }))[0],
    );
    await screen.findByText("Back to all trials");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const bobBox = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(bobBox);
    await userEvent.type(bobBox, "9");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(bob.record.hemoglobin_g_dl).toBe(9));
    expect(alice.record.hemoglobin_g_dl).toBe(13);
  });

  it("keeps showing the reader's value until the re-read lands, not until the write returns", async () => {
    // The row's own value only changes when the detail is re-read. Retired
    // any earlier, the OLD value goes back on screen for a whole round trip:
    // 13 → Saving… → 12 → 13, which is exactly the save-that-did-nothing
    // this overlay exists to prevent.
    const state = fakeState({ writable: WRITABLE });
    const api = await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");

    const release = api.holdNextDetail();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBe(13));

    // Written, answered, and the re-read still in the air.
    expect(screen.getByText("13")).toBeInTheDocument();
    expect(screen.queryByText("12")).toBeNull();

    release();
    await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
  });

  it("does not paint the written value on the × upper-limit row", async () => {
    // That row shows a RATIO of the same attribute. The control is withheld
    // there; the value must be too, or a clinically false number appears in a
    // table the patient reads.
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({ name: "hgb_min", label: "Hemoglobin" }),
        row({ name: "hgb_uln", label: "Hemoglobin ×ULN", ureadonly: true, uvalue: 0.8 }),
      ]),
    );
    renderTrialMatches(api, { state: state.adapter });
    await openDetail();
    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("13")).toBeInTheDocument();
    // The ratio row still reads its own number, and says nothing about saving.
    expect(screen.getByText("0.8")).toBeInTheDocument();
    expect(screen.getAllByText("Saving…")).toHaveLength(1);
  });

  it("does not let an older answer retire a newer edit to the same field", async () => {
    // Keyed by field alone, the first request's answer retires the second
    // edit's optimistic value: the row drops back to the server's older
    // number, with no "Saving…" on it, until the second re-read lands.
    const state = fakeState({ writable: WRITABLE });
    const releases: Array<() => void> = [];
    state.adapter.setPatientFields = vi.fn(
      (fields: Record<string, unknown>) =>
        new Promise<Record<string, WriteOutcome>>((resolve) => {
          releases.push(() =>
            resolve(
              Object.fromEntries(
                Object.entries(fields).map(([f, v]) => [f, { status: "saved", value: v }]),
              ),
            ),
          );
        }),
    );
    await startEditing(state);

    const type = async (value: string) => {
      const box = screen.getByRole("textbox", { name: "Hemoglobin" });
      await userEvent.clear(box);
      await userEvent.type(box, value);
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
    };
    await type("13");
    await waitFor(() => expect(releases.length).toBe(1));

    // A second edit while the first is still on the wire.
    await userEvent.click(screen.getByRole("button", { name: "Edit Hemoglobin" }));
    await type("14");
    expect(screen.getByText("14")).toBeInTheDocument();

    releases[0]();
    await waitFor(() => expect(releases.length).toBe(2));
    // Still the reader's newer value, still saving.
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("Saving…")).toBeInTheDocument();
  });

  it("blames the value that was refused, not the one still waiting", async () => {
    const state = fakeState({ writable: WRITABLE });
    const releases: Array<(fail: boolean) => void> = [];
    state.adapter.setPatientFields = vi.fn(
      (fields: Record<string, unknown>) =>
        new Promise<Record<string, WriteOutcome>>((resolve, reject) => {
          releases.push((fail) =>
            fail
              ? reject(new Error("403"))
              : resolve(
                  Object.fromEntries(
                    Object.entries(fields).map(([f, v]) => [
                      f,
                      { status: "saved", value: v },
                    ]),
                  ),
                ),
          );
        }),
    );
    await startEditing(state);
    const type = async (value: string) => {
      const box = screen.getByRole("textbox", { name: "Hemoglobin" });
      await userEvent.clear(box);
      await userEvent.type(box, value);
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
    };
    await type("13");
    await waitFor(() => expect(releases.length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Edit Hemoglobin" }));
    await type("14");

    releases[0](true);
    await waitFor(() => expect(releases.length).toBe(2));
    // The second edit is on its way; nothing is said about it yet, and the
    // row is still showing it rather than an error about a value the reader
    // has already moved past.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("14")).toBeInTheDocument();
  });

  it("retires the optimistic value under StrictMode, which is how it is mounted", async () => {
    // Regression: found by /qa on 2026-09-14 in the mock preview, where
    // "Saving…" never went away and no re-read was ever started.
    //
    // The writer claimed ownership by assigning a ref during render, inside
    // `useMemo`. React invokes that factory TWICE under StrictMode and keeps
    // one of the two results, so the ref named the instance that was thrown
    // away — and every callback of the one actually in use then failed its
    // own ownership check. The write went out, nothing was retired, and the
    // match never recomputed, which is the whole point of the phase.
    //
    // Every entry point in this repo mounts under StrictMode. No test did,
    // so the suite could not see it.
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <TrialMatches
            apiClient={api.client}
            queryClient={queryClient}
            patientInfo={{ disease: "multiple myeloma" }}
            personId="p1"
            state={state.adapter}
          />
        </QueryClientProvider>
      </StrictMode>,
    );

    await userEvent.click(
      (await screen.findAllByRole("button", { name: "View Trial" }))[0],
    );
    await screen.findByText("Back to all trials");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    const before = api.detailRequests().length;
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBe(13));
    // The two things the broken guard silently skipped.
    await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
    expect(api.detailRequests().length).toBeGreaterThan(before);
  });

  it("cancels without writing anything", async () => {
    const state = fakeState({ writable: WRITABLE });
    await startEditing(state);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "3");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("textbox", { name: "Hemoglobin" })).toBeNull();
    await new Promise((r) => setTimeout(r, 400));
    expect(state.adapter.setPatientFields).not.toHaveBeenCalled();
    expect(state.record).toEqual({});
  });
});

describe("telling the host what the record now says", () => {
  // The host owns the patient payload, and that payload wins server-side over
  // `personId`. So every query this remote makes after a write is answered
  // from the host's copy — and if the host never learns, the page goes on
  // describing the patient the reader just edited away from (#555). Only the
  // host can end that, so it has to be told.

  const editHemoglobin = async (
    state: ReturnType<typeof fakeState>,
    onPatientRecordChanged: (fields: Record<string, unknown>) => void,
    fields = [row()],
  ) => {
    const api = fakeApi();
    api.setDetail(detailWith(fields));
    const view = renderTrialMatches(api, { state: state.adapter, onPatientRecordChanged });
    await openDetail();
    await userEvent.click(
      await screen.findByRole("button", { name: `Edit ${fields[0].label}` }),
    );
    return { api, view };
  };

  it("tells the host what was written, once our own re-read has landed", async () => {
    // Not when the write returns. The host refreshes its payload on this
    // call, and a refresh that overlaps our own re-read has the page
    // answering from two different records for a round trip.
    const told = vi.fn();
    const state = fakeState({ writable: WRITABLE });
    const { api } = await editHemoglobin(state, told);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");

    const release = api.holdNextDetail();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBe(13));
    expect(told).not.toHaveBeenCalled();

    release();
    await waitFor(() => expect(told).toHaveBeenCalledWith({ hemoglobin_g_dl: 13 }));
  });

  it("reports what the RECORD says, not what was typed", async () => {
    // `differs` is a successful write whose stored value was canonicalised on
    // the way back. Handing the host the typed value would have it refresh
    // its payload with a number the record does not hold.
    const told = vi.fn();
    const state = fakeState({ writable: WRITABLE });
    state.adapter.setPatientFields = vi.fn(async (fields: Record<string, unknown>) =>
      Object.fromEntries(
        Object.keys(fields).map((f) => [f, { status: "differs" as const, value: 12.5 }]),
      ),
    );
    await editHemoglobin(state, told);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(told).toHaveBeenCalledWith({ hemoglobin_g_dl: 12.5 }));
  });

  it("says nothing about a field the record did not answer for", async () => {
    // `unconfirmed` is the record not mentioning the field — there is no
    // value to report, and reporting the typed one would push a write the
    // record may never have taken into the host's payload.
    const told = vi.fn();
    const state = fakeState({ writable: WRITABLE });
    state.adapter.setPatientFields = vi.fn(async () => ({
      hemoglobin_g_dl: { status: "unconfirmed" as const },
    }));
    const { api } = await editHemoglobin(state, told);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.detailRequests().length).toBeGreaterThan(1));
    expect(told).not.toHaveBeenCalled();
  });

  it("does not turn a confirmed write into a failed one when the host throws", async () => {
    // The host's refresh is not part of the write. A host that throws here
    // has failed to catch up — the page is as stale as it was before, which
    // is not the same as the save having failed, and must not be shown as
    // one.
    //
    // The throw is also not swallowed. It lands inside the settle chain,
    // where an uncaught one is an unhandled rejection: nothing on screen
    // changes either way, so the warning is the only thing that tells the
    // operator their host is not catching up.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const state = fakeState({ writable: WRITABLE });
    await editHemoglobin(state, () => {
      throw new Error("host blew up");
    });

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
    expect(state.record.hemoglobin_g_dl).toBe(13);
    // No "Couldn't save that", and the editor stays closed rather than
    // re-opening on the value to retry — both of which are how this page
    // says a write failed.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Hemoglobin" })).toBeNull();
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("the host could not be told"),
      expect.any(Error),
    );
    warn.mockRestore();
  });

  it("does not leave an unhandled rejection when the host's refresh is async", async () => {
    // The prop returns `void`, which an `async` function satisfies — and
    // refreshing a payload is exactly the kind of thing a host does
    // asynchronously. A rejection from one sails past a synchronous
    // `try`/`catch` and lands as an unhandled rejection, which some hosts
    // and test runners treat as fatal.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const state = fakeState({ writable: WRITABLE });
    await editHemoglobin(state, (() =>
      Promise.reject(new Error("refetch failed"))) as () => void);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(warn).toHaveBeenCalledWith(
        expect.stringContaining("the host could not be told"),
        expect.any(Error),
      ),
    );
    expect(screen.queryByRole("alert")).toBeNull();
    warn.mockRestore();
  });

  it("says nothing to a host that is no longer showing this patient", async () => {
    // The write still goes out — an edit the reader made and was told was
    // saving must not be dropped because they navigated — but the REPORT is
    // for a page that no longer exists. It carries no patient with it, so a
    // host receiving it after the switch would refresh its payload on values
    // belonging to whoever was on screen a moment ago.
    const told = vi.fn();
    const state = fakeState({ writable: WRITABLE });
    const { view } = await editHemoglobin(state, told);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    // Inside the queue's debounce, so the write leaves through the unmount
    // flush rather than having already gone.
    view.unmount();

    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBe(13));
    await new Promise((r) => setTimeout(r, 200));
    expect(told).not.toHaveBeenCalled();
  });

  it("tells the host developer when their payload names no patient", async () => {
    // The prop only works if the payload carries an id: without one, every
    // refresh reads as a new patient, so obeying "refresh your payload"
    // closes the trial page the edit was made from and silently drops the
    // report for anything edited while the refresh was in flight. Nothing
    // here can distinguish that from a real switch, so the host is told
    // rather than left to discover it in production.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    renderTrialMatches(api, {
      state: fakeState({ writable: WRITABLE }).adapter,
      patientInfo: { disease: "multiple myeloma" },
      onPatientRecordChanged: () => {},
    });
    await openDetail();

    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("carries no patient id"),
    );
    warn.mockRestore();
  });

  it("says nothing to a host that named one", async () => {
    // Non-vacuity: the warning must not fire for ht-phr's payload, which
    // carries `person_id` through `/normalize-ctomop-row/`.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    renderTrialMatches(api, {
      state: fakeState({ writable: WRITABLE }).adapter,
      patientInfo: { person_id: 9009, disease: "multiple myeloma" },
      onPatientRecordChanged: () => {},
    });
    await openDetail();

    expect(warn).not.toHaveBeenCalledWith(
      expect.stringContaining("carries no patient id"),
    );
    warn.mockRestore();
  });

  it("says nothing to a host that cannot edit at all", async () => {
    // No adapter means no editing pair, so the prop can never fire and
    // there is nothing for the host to act on. Warning anyway sends a host
    // that merely passes the callback around looking for a bug it does not
    // have.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    renderTrialMatches(api, {
      patientInfo: { disease: "multiple myeloma" },
      onPatientRecordChanged: () => {},
    });
    await openDetail();

    expect(warn).not.toHaveBeenCalledWith(
      expect.stringContaining("carries no patient id"),
    );
    warn.mockRestore();
  });

  it("says nothing once the host has taken the adapter away", async () => {
    // The other half of the guard above, and the one a host reaches without
    // unmounting: `state` gone means the pair is gone, which is how a host
    // spells "not this patient any more" (a logout, a switch mid-flight).
    // The write in hand is still finished — it was made and answered for the
    // patient who was on screen — but reporting it now would hand those
    // values to a host that has moved on.
    const told = vi.fn();
    const state = fakeState({ writable: WRITABLE });
    const { view } = await editHemoglobin(state, told);

    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    view.setProps({ state: undefined });

    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBe(13));
    await new Promise((r) => setTimeout(r, 200));
    expect(told).not.toHaveBeenCalled();
  });

  it("reports each batch on its own, not the batch before it as well", async () => {
    // The values are collected in a ref across a batch and handed over when
    // it settles. Left there, the next batch hands the host the previous
    // one's fields again — and by then they may be the values the host has
    // already caught up on, or ones a later edit has moved past.
    const told = vi.fn();
    const state = fakeState({
      writable: {
        ...WRITABLE,
        platelet_count: { kind: "direct", writable: true, value_kind: "number" },
      },
    });
    await editHemoglobin(state, told, [
      row(),
      row({ name: "plt", label: "Platelets", upatientField: "platelet_count", uvalue: 150 }),
    ]);

    const hgb = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(hgb);
    await userEvent.type(hgb, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(told).toHaveBeenCalledTimes(1));
    expect(told).toHaveBeenLastCalledWith({ hemoglobin_g_dl: 13 });

    await userEvent.click(await screen.findByRole("button", { name: "Edit Platelets" }));
    const plt = screen.getByRole("textbox", { name: "Platelets" });
    await userEvent.clear(plt);
    await userEvent.type(plt, "160");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(told).toHaveBeenCalledTimes(2));
    expect(told).toHaveBeenLastCalledWith({ platelet_count: 160 });
  });

  it("does not wipe a second editor when the host acts on being told", async () => {
    // The host's answer to this callback is to refresh its payload, and the
    // payload is in the key of both the detail read and the writable-fields
    // descriptor. Keyed on the whole payload, the refresh empties both: the
    // attribute rows unmount and the controls are withheld, so an editor the
    // reader has open on ANOTHER field — the normal way to work through a
    // column of them — closes on whatever they had typed into it.
    //
    // Measured before the fix: the Platelets box and its "160" were gone,
    // and identical to the no-refresh control after it.
    const state = fakeState({
      writable: {
        ...WRITABLE,
        platelet_count: { kind: "direct", writable: true, value_kind: "number" },
      },
    });
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row(),
        row({ name: "plt", label: "Platelets", upatientField: "platelet_count", uvalue: 150 }),
      ]),
    );
    // A host that does what the prop documents: re-read the profile, hand
    // back a new payload. Same person, one year older.
    let age = 50;
    const patientOf = (years: number) => ({
      person_id: 9009,
      disease: "multiple myeloma",
      patient_age: years,
    });
    const view = renderTrialMatches(api, {
      state: state.adapter,
      patientInfo: patientOf(age),
      onPatientRecordChanged: () => {
        age += 1;
        view.setProps({ patientInfo: patientOf(age) });
      },
    });
    await openDetail();

    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const hgb = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(hgb);
    await userEvent.type(hgb, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // Opened while the write, the re-read and the host's refresh are all
    // still on their way.
    await userEvent.click(await screen.findByRole("button", { name: "Edit Platelets" }));
    const plt = screen.getByRole("textbox", { name: "Platelets" });
    await userEvent.clear(plt);
    await userEvent.type(plt, "160");

    await waitFor(() => expect(state.record.hemoglobin_g_dl).toBe(13));
    await waitFor(() => expect(age).toBe(51));
    await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());

    expect(screen.getByRole("textbox", { name: "Platelets" })).toHaveValue("160");
  });

  it("ends with the row showing what the record took — #555 itself", async () => {
    // Everything else here is about collateral damage. This is the symptom:
    // the reader edits a field, the write lands, and the page goes back to
    // the old value because every query carries the host's payload and the
    // server answers from it.
    //
    // The fake plays the server honestly: its detail answers 12 until the
    // host refreshes its payload, and 13 afterwards. So the row reads 13 at
    // the end only if the refresh actually happened and was answered.
    const state = fakeState({ writable: WRITABLE });
    const api = fakeApi();
    api.setDetail(detailWith([row({ uvalue: 12 })]));
    let age = 50;
    const patientOf = (years: number) => ({
      person_id: 9009,
      disease: "multiple myeloma",
      patient_age: years,
    });
    const view = renderTrialMatches(api, {
      state: state.adapter,
      patientInfo: patientOf(age),
      onPatientRecordChanged: (fields) => {
        api.setDetail(detailWith([row({ uvalue: fields.hemoglobin_g_dl as number })]));
        age += 1;
        view.setProps({ patientInfo: patientOf(age) });
      },
    });
    await openDetail();

    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const box = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(box);
    await userEvent.type(box, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(age).toBe(51));
    await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
    // Not the optimistic value: that is long retired by now, and 12 is what
    // the page showed before.
    expect(await screen.findByText("13")).toBeInTheDocument();
    expect(screen.queryByText("12")).toBeNull();
  });

  it("finishes a second edit made while the host was catching up", async () => {
    // The host's refresh arrives in the middle of the next edit. Keyed on the
    // payload hash, the queue is rebuilt by it: the writer carrying this edit
    // is no longer the current one, so its answer is discarded on arrival —
    // the optimistic value is never retired, the row keeps saying "Saving…"
    // over a write that succeeded, and the host is never told about it.
    const state = fakeState({
      writable: {
        ...WRITABLE,
        platelet_count: { kind: "direct", writable: true, value_kind: "number" },
      },
    });
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row(),
        row({ name: "plt", label: "Platelets", upatientField: "platelet_count", uvalue: 150 }),
      ]),
    );
    const told = vi.fn();
    let age = 50;
    const patientOf = (years: number) => ({
      person_id: 9009,
      disease: "multiple myeloma",
      patient_age: years,
    });
    // The host's refresh is held rather than applied where it is asked for,
    // so the test can drop it into the middle of the NEXT edit. Applied as
    // soon as it is asked for, it lands between the two batches, where the
    // key does not matter — which is why this needs arranging by hand.
    let refresh: (() => void) | null = null;
    const view = renderTrialMatches(api, {
      state: state.adapter,
      patientInfo: patientOf(age),
      onPatientRecordChanged: (fields) => {
        told(fields);
        refresh = () => {
          age += 1;
          view.setProps({ patientInfo: patientOf(age) });
        };
      },
    });
    await openDetail();

    await userEvent.click(await screen.findByRole("button", { name: "Edit Hemoglobin" }));
    const hgb = screen.getByRole("textbox", { name: "Hemoglobin" });
    await userEvent.clear(hgb);
    await userEvent.type(hgb, "13");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(told).toHaveBeenCalledTimes(1));

    await userEvent.click(await screen.findByRole("button", { name: "Edit Platelets" }));
    const plt = screen.getByRole("textbox", { name: "Platelets" });
    await userEvent.clear(plt);
    await userEvent.type(plt, "160");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    // Queued, not yet on the wire — the queue debounces. This is the window.
    refresh!();

    await waitFor(() => expect(state.record.platelet_count).toBe(160));
    await waitFor(() => expect(told).toHaveBeenLastCalledWith({ platelet_count: 160 }));
    await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
  });
});
