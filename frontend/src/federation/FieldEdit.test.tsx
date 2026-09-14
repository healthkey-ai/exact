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
