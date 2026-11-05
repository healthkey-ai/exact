import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { createPromopState } from "./state";
import { GenomicsFindingsDialog } from "./GenomicsFindingsDialog";

const finding = {
  id: 1,
  gene: "BRCA1",
  variant: "c.68_69delAG",
  origin: "Germline",
  interpretation: "Pathogenic",
  status: "present",
};

function adapter(overrides: Record<string, unknown> = {}) {
  return {
    list: vi.fn().mockResolvedValue([finding]),
    catalog: vi.fn().mockResolvedValue({
      markers: [{ key: "brca1", field_name: "genomics_brca1", gene: "BRCA1", label: "BRCA1", kind: "gene", writable: true }],
    }),
    create: vi.fn().mockResolvedValue({ ...finding, id: 2 }),
    update: vi.fn().mockResolvedValue(finding),
    remove: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function renderDialog(genomics = adapter()) {
  const onGenomicsChanged = vi.fn();
  render(
    <GenomicsFindingsDialog
      label="Mutation Genes"
      editing={{
        save: vi.fn(),
        outstanding: {},
        failed: {},
        fields: { genetic_mutations: { kind: "editable", writable: true } },
        genomics,
        onGenomicsChanged,
      }}
      onClose={() => {}}
    />,
  );
  return { genomics, onGenomicsChanged };
}

describe("the genomics adapter", () => {
  it("speaks the dedicated findings endpoint, not a PatientRecord PATCH", async () => {
    const client = {
      get: vi.fn(async (url: string) =>
        url.endsWith("genomics-catalog/")
          ? { data: { markers: [] } }
          : { data: [] },
      ),
      post: vi.fn(async (_url: string, body: unknown) => ({ data: { id: 1, ...(body as object) } })),
      patch: vi.fn(async (_url: string, body: unknown) => ({ data: { id: 1, ...(body as object) } })),
      delete: vi.fn(async () => ({ data: {} })),
    };
    const state = createPromopState({
      client: client as never,
      personId: 9010,
      basePath: "/v1",
    });

    await state.genomics!.list();
    await state.genomics!.catalog();
    await state.genomics!.create({ gene: "BRCA1", variant: "c.68_69delAG" });
    await state.genomics!.update(7, { gene: "BRCA1" });
    await state.genomics!.remove(7);

    expect(client.get).toHaveBeenCalledWith("/v1/patient-records/9010/genomics/");
    expect(client.get).toHaveBeenCalledWith("/v1/patient-records/9010/genomics-catalog/");
    expect(client.post).toHaveBeenCalledWith(
      "/v1/patient-records/9010/genomics/",
      { gene: "BRCA1", variant: "c.68_69delAG" },
    );
    expect(client.patch).toHaveBeenCalledWith(
      "/v1/patient-records/9010/genomics/7/",
      { gene: "BRCA1" },
    );
    expect(client.delete).toHaveBeenCalledWith("/v1/patient-records/9010/genomics/7/");
  });
});

describe("GenomicsFindingsDialog", () => {
  it("lists findings and saves an edit through the adapter", async () => {
    const { genomics, onGenomicsChanged } = renderDialog();

    expect(await screen.findByText(/c\.68_69delAG/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit BRCA1" }));

    const variant = screen.getByLabelText("Variant / finding");
    await userEvent.clear(variant);
    await userEvent.type(variant, "c.5266dupC");
    await userEvent.click(screen.getByRole("button", { name: "Save finding" }));

    await waitFor(() =>
      expect(genomics.update).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ gene: "BRCA1", variant: "c.5266dupC" }),
      ),
    );
    expect(onGenomicsChanged).toHaveBeenCalled();
  });

  it("does not offer an editor when the descriptor is read-only", async () => {
    render(
      <GenomicsFindingsDialog
        label="Mutation Genes"
        editing={{
          save: vi.fn(),
          outstanding: {},
          failed: {},
          fields: { genetic_mutations: { kind: "editable", writable: false } },
          genomics: adapter(),
        }}
        onClose={() => {}}
      />,
    );

    expect(await screen.findByText("You can view these findings, but not edit them.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add finding" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit BRCA1" })).not.toBeInTheDocument();
  });

  it("preserves a structural finding's feature type on save", async () => {
    const structural = {
      id: 2,
      gene: "17P",
      genomic_feature: "17p",
      feature_type: "Chromosome arm/region",
      variant: "del17p",
    };
    const { genomics } = renderDialog(adapter({ list: vi.fn().mockResolvedValue([structural]) }));

    await userEvent.click(await screen.findByRole("button", { name: "Edit 17P" }));
    await userEvent.click(screen.getByRole("button", { name: "Save finding" }));

    await waitFor(() =>
      expect(genomics.update).toHaveBeenCalledWith(
        2,
        expect.objectContaining({
          genomic_feature: "17p",
          feature_type: "Chromosome arm/region",
        }),
      ),
    );
  });

  it("drops a stale marker_key when the gene changes", async () => {
    const marked = {
      id: 3,
      gene: "BRCA1",
      marker_key: "brca1",
      variant: "c.68_69delAG",
      origin: "Germline",
      interpretation: "Pathogenic",
      status: "present",
    };
    const { genomics } = renderDialog(adapter({ list: vi.fn().mockResolvedValue([marked]) }));

    await userEvent.click(await screen.findByRole("button", { name: "Edit BRCA1" }));
    const gene = screen.getByLabelText("Gene");
    await userEvent.clear(gene);
    await userEvent.type(gene, "TP53");
    await userEvent.click(screen.getByRole("button", { name: "Save finding" }));

    await waitFor(() => expect(genomics.update).toHaveBeenCalled());
    const payload = (genomics.update as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(payload.marker_key).toBeUndefined();
  });

  it("adds a finding and keeps the gene choices from the catalog", async () => {
    const { genomics } = renderDialog(adapter({ list: vi.fn().mockResolvedValue([]) }));

    await userEvent.click(await screen.findByRole("button", { name: "Add finding" }));
    const genes = [...document.querySelectorAll("#exact-genomics-genes option")].map(
      (option) => (option as HTMLOptionElement).value,
    );
    expect(genes).toContain("BRCA1");
    await userEvent.type(screen.getByLabelText("Gene"), "TP53");
    await userEvent.type(screen.getByLabelText("Variant / finding"), "c.524G>A");
    await userEvent.click(screen.getByRole("button", { name: "Save finding" }));

    await waitFor(() =>
      expect(genomics.create).toHaveBeenCalledWith(
        expect.objectContaining({
          gene: "TP53",
          genomic_feature: "TP53",
          variant: "c.524G>A",
        }),
      ),
    );
  });
});
