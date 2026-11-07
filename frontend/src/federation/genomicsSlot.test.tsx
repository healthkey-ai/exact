// The host genomics slot: a host that owns the genomic-findings writer (CB,
// through PROMOP) draws its own editor, and EXACT must not require its own
// `genomics` adapter — nor fall back to `GenomicsFindingsDialog` — when the
// slot is supplied.
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

const genomicsRow: TrialDetailField = {
  name: "mutationGenes",
  label: "Mutation Genes",
  type: "string",
  value: "BRCA1",
  ufield: "mutationGenes",
  upatientField: "genetic_mutations",
  matchingType: "matched",
  subform_details: [
    {
      name: "geneticMutations",
      label: "Mutations",
      type: "string",
      value: null,
      upatientField: "genetic_mutations",
    },
  ],
};

const detailWithRow = () =>
  trialDetail(1, {
    details: { trialEligibilityAttributes: [genomicsRow] },
  } as never);

const open = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
};

const hostSlot = () => {
  const render = vi.fn(
    ({ label, onClose }: { label: string; onClose: () => void }) => (
      <div>
        <p>host genomics editor for {label}</p>
        <button type="button" onClick={onClose}>
          Close host editor
        </button>
      </div>
    ),
  );
  return { render };
};

describe("the host genomics slot", () => {
  it("opens the host's editor without EXACT's genomics adapter", async () => {
    // No `state`/`patientRecord`, so `editing` is undefined: EXACT has no
    // writer of its own. The slot alone is enough to offer the pencil.
    const api = fakeApi();
    api.setDetail(detailWithRow());
    const { render } = hostSlot();
    renderTrialMatches(api, { renderGenomicsEditor: render });

    await open();
    await userEvent.click(
      screen.getByRole("button", { name: "Edit Mutation Genes genetic findings" }),
    );

    expect(screen.getByText("host genomics editor for Mutation Genes")).toBeInTheDocument();
    expect(render).toHaveBeenCalledWith(
      expect.objectContaining({ label: "Mutation Genes", trialId: expect.anything() }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Close host editor" }));
    expect(screen.queryByText("host genomics editor for Mutation Genes")).toBeNull();
  });

  it("wins over EXACT's dialog when the host also supplies a writer", async () => {
    const api = fakeApi();
    api.setDetail(detailWithRow());
    const { render } = hostSlot();
    const genomics = {
      list: vi.fn(),
      catalog: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
    };
    const state = fakeState({
      writable: {
        genetic_mutations: {
          kind: "editable",
          writable: true,
          target: "genomics",
        },
      },
      overrides: { genomics },
    });
    renderTrialMatches(api, {
      state: state.adapter,
      renderGenomicsEditor: render,
    });

    await open();
    await userEvent.click(
      screen.getByRole("button", { name: "Edit Mutation Genes genetic findings" }),
    );

    expect(screen.getByText("host genomics editor for Mutation Genes")).toBeInTheDocument();
    // The slot REPLACES the dialog: both on screen would be two editors for
    // one row, and the built-in one would read findings through its adapter.
    expect(
      screen.queryByRole("dialog", { name: "Genetic findings for Mutation Genes" }),
    ).toBeNull();
    expect(genomics.list).not.toHaveBeenCalled();
  });

  it("draws no pencil for a read-only host that supplied no slot", async () => {
    const api = fakeApi();
    api.setDetail(detailWithRow());
    renderTrialMatches(api);

    await open();
    expect(
      screen.queryByRole("button", { name: "Edit Mutation Genes genetic findings" }),
    ).toBeNull();
  });
});
