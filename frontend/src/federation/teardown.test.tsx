/** Timers and settles that outlive the page must not reach React (#728).
 *
 *  CI failed once with every test green and an uncaught
 *  `ReferenceError: window is not defined`. See `errorsAfterTeardown`.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeApi, fakeState, renderTrialMatches } from "../test/renderTrialMatches";
import { errorsAfterTeardown } from "../test/afterTeardown";
import { SAVED_FILTERS_GRACE_MS } from "./hooks";
import { GenomicsFindingsDialog } from "./GenomicsFindingsDialog";

describe("after the page is gone (#728)", () => {
  it("the saved-filters grace timer does not fire into React", async () => {
    renderTrialMatches(fakeApi(), { state: fakeState().adapter });
    await screen.findByText("Trial 1");
    // Unmounted before the cap's 300 ms are up: the cap is still pending.
    cleanup();
    const escaped = await errorsAfterTeardown(() => {}, SAVED_FILTERS_GRACE_MS + 100);
    expect(escaped).toEqual([]);
  });

  it("an export answered after the page is gone starts no download and touches nothing", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    const release = api.holdNextExport();
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    cleanup();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click");
    try {
      const escaped = await errorsAfterTeardown(() => release(), 100);
      expect(escaped).toEqual([]);
      expect(click).not.toHaveBeenCalled();
    } finally {
      click.mockRestore();
    }
  });

  it("a genomics save answered after the dialog is gone touches nothing", async () => {
    const finding = { id: 1, gene: "BRCA1", variant: "c.68", status: "present" };
    let release: (() => void) | null = null;
    const genomics = {
      list: vi.fn().mockResolvedValue([finding]),
      catalog: vi.fn().mockResolvedValue({ markers: [] }),
      create: vi.fn(),
      update: vi.fn(() => new Promise((resolve) => { release = () => resolve(finding); })),
      remove: vi.fn(),
    };
    render(
      <GenomicsFindingsDialog
        label="Mutation Genes"
        editing={{
          save: vi.fn(), outstanding: {}, failed: {},
          fields: { genetic_mutations: { kind: "editable", writable: true } } as never,
          genomics: genomics as never, onGenomicsChanged: vi.fn(),
        } as never}
        onClose={() => {}}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit BRCA1" }));
    await userEvent.click(screen.getByRole("button", { name: "Save finding" }));
    await waitFor(() => expect(release).not.toBeNull());
    cleanup();
    expect(await errorsAfterTeardown(() => release!(), 50)).toEqual([]);
  });
});
