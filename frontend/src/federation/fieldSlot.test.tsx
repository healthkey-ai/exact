// The per-row host editor slot. CB returns an empty `data-promop-field` node
// for a row and lets one PROMOP `mountFields` root portal an editor into each;
// EXACT must then not also draw its own inline editor (or negated dialog) for
// that row.
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  fakeApi,
  fakeState,
  renderTrialMatches,
  trialDetail,
} from "../test/renderTrialMatches";
import type { TrialDetailField } from "./types";

const row = (over: Partial<TrialDetailField> = {}): TrialDetailField => ({
  name: "hemoglobinLevel",
  label: "Hemoglobin",
  type: "number",
  value: 12,
  ufield: "hemoglobinLevel",
  upatientField: "hemoglobin_g_dl",
  matchingType: "matched",
  uvalue: 11,
  utype: "number",
  ...over,
});

const detailWith = (fields: TrialDetailField[]) =>
  trialDetail(1, {
    details: { trialEligibilityAttributes: fields },
  } as never);

const open = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
  await screen.findByRole("heading", { level: 1, name: "Trial 1" });
};

const writable = () =>
  fakeState({
    writable: {
      hemoglobin_g_dl: { kind: "direct", writable: true, value_kind: "number" },
    },
  });

// The explicit-answer row and its descriptor: an attestation EXACT writes
// through its own endpoint, not a host field save.
const explicitRow = () =>
  row({
    name: "preExistingConditionsExcluded",
    label: "Pre Existing Conditions Excluded",
    type: "multiselect",
    value: ["cardiacIssues"],
    ufield: "preExistingConditionCategories",
    upatientField: "pre_existing_condition_categories",
    ureadonly: false,
    upatientRecomputed: false,
    uvalue: [],
    utype: "multiselect",
    matchingType: "unknown",
  });

const explicitState = () =>
  fakeState({
    writable: {
      pre_existing_condition_categories: {
        kind: "explicit-answer",
        writable: false,
        answerable: true,
        target: "patient_record",
        endpoint: "preexisting-conditions-answer",
        actions: ["confirm_none", "set_categories", "withdraw"],
        action_labels: {
          confirm_none: "No pre-existing conditions",
          set_categories: "Save these conditions",
          withdraw: "Withdraw answer",
        },
        value_kind: "string",
        multiple: true,
        options: [{ value: "cardiacIssues", label: "Cardiac Issues" }],
      },
    },
    overrides: { explicitAnswers: { answer: vi.fn(async () => undefined) } },
  });

describe("the host field editor slot", () => {
  it("renders the host node per row and suppresses EXACT's value and editor", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([row({ uvalue: 4242 })]));
    const state = writable();
    const slot = vi.fn(({ field, label }: { field: string; label: string }) => (
      <div data-testid={`host-field-${field}`}>{label}</div>
    ));
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: slot,
      fieldEditorOwnsValue: () => true,
    });

    await open();

    expect(screen.getByTestId("host-field-hemoglobinLevel")).toBeInTheDocument();
    expect(slot).toHaveBeenCalledWith(
      expect.objectContaining({
        field: "hemoglobinLevel",
        label: "Hemoglobin",
        trialId: 1,
      }),
    );
    // One editor, not two: EXACT's own pencil would open a second writer.
    expect(screen.queryByRole("button", { name: "Edit Hemoglobin" })).toBeNull();
    // And one value, not two: the host widget renders the patient's value, so
    // EXACT must not paint its own beside it (the native CB cell shows the
    // control or the value once, never both).
    expect(screen.queryByText("4242")).toBeNull();
  });

  it("still draws EXACT's pencil when the host passes no slot", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    const state = writable();
    renderTrialMatches(api, { state: state.adapter, stateIdentity: "patient-1" });

    await open();
    expect(screen.getByRole("button", { name: "Edit Hemoglobin" })).toBeInTheDocument();
  });

  it("keeps the mismatch mark, beside the requirement rather than the control", async () => {
    // The mark is the only non-colour signal for a mismatch, so it must stay.
    // It moves to the Required cell: in the value cell it would float in front
    // of the host's control as a bare "✕ Select…", which is neither CB's
    // native layout nor a sentence a reader can place.
    const api = fakeApi();
    api.setDetail(detailWith([row({ matchingType: "not_matched" })]));
    renderTrialMatches(api, {
      renderFieldEditor: () => <div data-testid="host-field" />,
      fieldEditorOwnsValue: () => true,
    });

    await open();
    expect(screen.getByTestId("host-field")).toBeInTheDocument();
    const marker = screen.getByRole("img", { name: "does not match" });
    expect(marker).toHaveTextContent("✕");
    expect(marker.closest(".exact-elig__cell")).toHaveAttribute("data-col", "required");
  });

  it("keeps EXACT's value when the host only supplies an editor", async () => {
    // The opt-in is what says the node renders the value. Without it the slot
    // replaces the editor alone — the shape every existing host has.
    const api = fakeApi();
    api.setDetail(detailWith([row({ uvalue: 4242 })]));
    renderTrialMatches(api, {
      renderFieldEditor: () => <div data-testid="host-field" />,
    });

    await open();
    expect(screen.getByTestId("host-field")).toBeInTheDocument();
    expect(screen.getByText("4242")).toBeInTheDocument();
  });

  it("keeps the value for a row whose slot is editor-only", async () => {
    // One slot, two shapes: CB's therapy rows get a pencil that draws no
    // value, so the per-row predicate must keep EXACT's value there.
    const api = fakeApi();
    api.setDetail(detailWith([row({ uvalue: 4242, uunits: "g/dL" })]));
    renderTrialMatches(api, {
      renderFieldEditor: () => <button type="button">Edit therapy</button>,
      fieldEditorOwnsValue: ({ field }) => field !== "hemoglobinLevel",
    });

    await open();
    expect(screen.getByText("4242")).toBeInTheDocument();
    expect(screen.getByText("g/dL")).toBeInTheDocument();
  });

  it("shows a host widget's value once, not beside EXACT's copy", async () => {
    // The real CB widget renders the value itself; assert the whole cell reads
    // it once when the host says so.
    const api = fakeApi();
    api.setDetail(detailWith([row({ uvalue: 4242, uunits: "g/dL" })]));
    renderTrialMatches(api, {
      renderFieldEditor: () => (
        <div data-testid="host-field">4242 g/dL</div>
      ),
      fieldEditorOwnsValue: () => true,
    });

    await open();
    expect(screen.getAllByText("4242 g/dL")).toHaveLength(1);
    expect(screen.queryByText("g/dL", { selector: ".exact-elig__units" })).toBeNull();
    // No separate EXACT value beside the widget's copy.
    expect(document.querySelector('[data-col="yours"] .exact-elig__val')).toBeNull();
  });

  it("falls back to the row name and to EXACT's editor for a row the host declines", async () => {
    // Returning null says "the host does not own THIS row", not "nobody may
    // edit it". A host that only covers therapy lines must not take EXACT's
    // scalar editor away from every other row.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({
          ufield: null,
          name: "creatinineLevel",
          label: "Creatinine",
          upatientField: "creatinine_mg_dl",
          utype: "number",
          uunits: "mg/dL",
        }),
      ]),
    );
    const state = fakeState({
      writable: {
        creatinine_mg_dl: { kind: "direct", writable: true, value_kind: "number" },
      },
    });
    const slot = vi.fn(() => null);
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: slot,
      fieldEditorOwnsValue: () => true,
    });

    await open();
    expect(slot).toHaveBeenCalledWith(
      expect.objectContaining({ field: "creatinineLevel", label: "Creatinine" }),
    );
    expect(screen.getByRole("button", { name: "Edit Creatinine" })).toBeInTheDocument();
    // No host node drew the value, so EXACT keeps painting it even with the
    // opt-in set.
    expect(screen.getByText("11")).toBeInTheDocument();
    expect(screen.getByText("mg/dL")).toBeInTheDocument();
  });

  it("keeps EXACT's editor on an uncovered row while the host covers another", async () => {
    // The partial-slot case: a host adapter that only has a therapy editor
    // returns a node for that row and null for the scalar beside it.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({ uvalue: 4242 }),
        row({
          name: "creatinineLevel",
          label: "Creatinine",
          ufield: "creatinineLevel",
          upatientField: "creatinine_mg_dl",
        }),
      ]),
    );
    const state = fakeState({
      writable: {
        hemoglobin_g_dl: { kind: "direct", writable: true, value_kind: "number" },
        creatinine_mg_dl: { kind: "direct", writable: true, value_kind: "number" },
      },
    });
    const slot = vi.fn(({ field }: { field: string }) =>
      field === "hemoglobinLevel" ? <div data-testid="host-field" /> : null,
    );
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: slot,
      fieldEditorOwnsValue: () => true,
    });

    await open();
    expect(screen.getByTestId("host-field")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Hemoglobin" })).toBeNull();
    expect(screen.getByRole("button", { name: "Edit Creatinine" })).toBeInTheDocument();
  });

  it("lets a host suppress one row's editor with an empty node", async () => {
    // The escape hatch the fallback needs: a node is an ownership claim, so an
    // empty one suppresses EXACT's editor without drawing anything.
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    renderTrialMatches(api, {
      state: writable().adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: () => <></>,
      fieldEditorOwnsValue: () => false,
    });

    await open();
    expect(screen.queryByRole("button", { name: "Edit Hemoglobin" })).toBeNull();
    // Nothing drew the value, so EXACT still does.
    expect(screen.getByText("11")).toBeInTheDocument();
  });

  it("calls the slot for every row", async () => {
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row(),
        row({
          name: "creatinineLevel",
          label: "Creatinine",
          ufield: "creatinineLevel",
          upatientField: "creatinine_mg_dl",
        }),
      ]),
    );
    const slot = vi.fn(({ field }: { field: string }) => (
      <div data-testid={`host-field-${field}`} />
    ));
    renderTrialMatches(api, { renderFieldEditor: slot });

    await open();
    expect(screen.getByTestId("host-field-hemoglobinLevel")).toBeInTheDocument();
    expect(screen.getByTestId("host-field-creatinineLevel")).toBeInTheDocument();
  });

  it("draws EXACT's negated dialog without the slot", async () => {
    // The baseline the suppression test needs: without it, an absent button
    // could mean the row was never editable in the first place.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({
          name: "noHiv",
          label: "No HIV",
          type: "boolean",
          value: true,
          ufield: "noHivStatus",
          upatientField: "no_hiv_status",
          utype: "boolean",
          uvalue: false,
          negative_source: {
            upatientField: "hiv_status",
            type: "boolean",
            available: true,
            value: true,
          },
        }),
      ]),
    );
    const state = fakeState({
      writable: {
        no_hiv_status: {
          kind: "negated",
          writable: true,
          canonical: "hiv_status",
          inverted: true,
          value_kind: "boolean",
          inputs: ["hiv_status"],
        },
        hiv_status: {
          kind: "direct",
          writable: true,
          target: "patient_record",
          value_kind: "boolean",
          answer_labels: { true: "HIV Positive", false: "No HIV" },
        },
      },
    });
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
    });

    await open();
    expect(screen.getByRole("button", { name: "Answer No HIV" })).toBeInTheDocument();
  });

  it("suppresses EXACT's negated boolean dialog too", async () => {
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({
          name: "noHiv",
          label: "No HIV",
          type: "boolean",
          value: true,
          ufield: "noHivStatus",
          upatientField: "no_hiv_status",
          utype: "boolean",
          uvalue: false,
          negative_source: {
            upatientField: "hiv_status",
            type: "boolean",
            available: true,
            value: true,
          },
        }),
      ]),
    );
    const state = fakeState({
      writable: {
        no_hiv_status: {
          kind: "negated",
          writable: true,
          canonical: "hiv_status",
          inverted: true,
          value_kind: "boolean",
          inputs: ["hiv_status"],
        },
        hiv_status: {
          kind: "direct",
          writable: true,
          target: "patient_record",
          value_kind: "boolean",
          answer_labels: { true: "HIV Positive", false: "No HIV" },
        },
      },
    });
    const slot = vi.fn(() => <div data-testid="host-field-no-hiv" />);
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: slot,
    });

    await open();
    expect(screen.getByTestId("host-field-no-hiv")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Answer No HIV" })).toBeNull();
  });

  it("keeps EXACT's negated dialog on a row the slot declines", async () => {
    // Same row as above, with the slot returning null: the host covers some
    // other row, not this one, so the negation keeps its door.
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({
          name: "noHiv",
          label: "No HIV",
          type: "boolean",
          value: true,
          ufield: "noHivStatus",
          upatientField: "no_hiv_status",
          utype: "boolean",
          uvalue: false,
          negative_source: {
            upatientField: "hiv_status",
            type: "boolean",
            available: true,
            value: true,
          },
        }),
      ]),
    );
    const state = fakeState({
      writable: {
        no_hiv_status: {
          kind: "negated",
          writable: true,
          canonical: "hiv_status",
          inverted: true,
          value_kind: "boolean",
          inputs: ["hiv_status"],
        },
        hiv_status: {
          kind: "direct",
          writable: true,
          target: "patient_record",
          value_kind: "boolean",
          answer_labels: { true: "HIV Positive", false: "No HIV" },
        },
      },
    });
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: () => null,
    });

    await open();
    expect(screen.getByRole("button", { name: "Answer No HIV" })).toBeInTheDocument();
  });

  it("keeps the explicit-answer door on a row the slot declines", async () => {
    // The attestation is EXACT's own writer, not the host's field save. A
    // partial host slot must not hide it on the rows it does not cover.
    const api = fakeApi();
    api.setDetail(detailWith([explicitRow()]));
    const state = explicitState();
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: () => null,
    });

    await open();
    expect(
      screen.getByRole("button", { name: "Answer Pre Existing Conditions Excluded" }),
    ).toBeInTheDocument();
  });

  it("suppresses the explicit-answer door on a row the slot owns", async () => {
    // The pair the fallback needs: an owned row must not offer two writers,
    // one of which carries an attestation snapshot.
    const api = fakeApi();
    api.setDetail(detailWith([explicitRow()]));
    const state = explicitState();
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: () => <div data-testid="host-field" />,
    });

    await open();
    expect(screen.getByTestId("host-field")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Answer Pre Existing Conditions Excluded" }),
    ).toBeNull();
  });
});
