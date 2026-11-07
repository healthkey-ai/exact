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

describe("the host field editor slot", () => {
  it("renders the host node per row and suppresses EXACT's inline editor", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    const state = writable();
    const slot = vi.fn(({ field, label }: { field: string; label: string }) => (
      <div data-testid={`host-field-${field}`}>{label}</div>
    ));
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderFieldEditor: slot,
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
  });

  it("still draws EXACT's pencil when the host passes no slot", async () => {
    const api = fakeApi();
    api.setDetail(detailWith([row()]));
    const state = writable();
    renderTrialMatches(api, { state: state.adapter, stateIdentity: "patient-1" });

    await open();
    expect(screen.getByRole("button", { name: "Edit Hemoglobin" })).toBeInTheDocument();
  });

  it("falls back to the row name and lets the host decline a row", async () => {
    const api = fakeApi();
    api.setDetail(
      detailWith([
        row({
          ufield: null,
          name: "creatinineLevel",
          label: "Creatinine",
          upatientField: "creatinine_mg_dl",
          utype: "number",
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
    });

    await open();
    expect(slot).toHaveBeenCalledWith(
      expect.objectContaining({ field: "creatinineLevel", label: "Creatinine" }),
    );
    // Returning null is a refusal, not a fallback: EXACT's own pencil must
    // not come back for a row whose editing the host has taken over.
    expect(screen.queryByRole("button", { name: "Edit Creatinine" })).toBeNull();
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
});
