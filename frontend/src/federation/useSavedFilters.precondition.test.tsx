// Does the HOOK send a precondition — not "does the helper it should call".
//
// This is the one that would have caught the defect the seam was written
// for: `useSavedFilters` built its own three-method literal, the new
// property never left the building, and every transport and seam test
// stayed green while production wrote unconditionally. Reverting the hook
// to that literal fails nothing else in the suite; it fails this.

import { describe, expect, it } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import { useSavedFilters } from "./hooks";
import type { Precondition, TrialStateAdapter } from "./state";
import type { FilterState } from "./types";

function versionedAdapter(seen: Precondition[]): TrialStateAdapter {
  let row: Record<string, unknown> = { sponsor: "Acme" };
  let tag: string | null = '"v0"';
  return {
    listFavoriteIds: async () => [],
    setFavorite: async () => undefined,
    listRegisteredIds: async () => [],
    setRegistered: async () => undefined,
    listAdvancedEnrollments: async () => ({}),
    getPreferences: async () => row as never,
    savePreferences: async () => undefined,
    resetPreferences: async () => undefined,
    getWritableFields: async () => ({}) as never,
    preferenceVersioning: {
      read: async () => ({ filters: { ...row } as never, version: tag }),
      write: async (filters: FilterState, precondition: Precondition) => {
        seen.push(precondition);
        row = { ...(filters as object) };
        tag = '"v1"';
        return tag;
      },
      clear: async (precondition: Precondition) => {
        seen.push(precondition);
        row = {};
        tag = '"cleared"';
        return tag;
      },
    },
  } as unknown as TrialStateAdapter;
}

describe("useSavedFilters", () => {
  it("writes conditionally, with the version it read", async () => {
    const seen: Precondition[] = [];
    const state = versionedAdapter(seen);

    const { result } = renderHook(() =>
      useSavedFilters(state, "patient-1", () => undefined),
    );
    await waitFor(() => expect(result.current).toBeTruthy());

    act(() => {
      result.current.persist({ distance: 50 } as never);
    });

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({ kind: "ifMatch", version: '"v0"' });
  });

  it("resets conditionally too", async () => {
    const seen: Precondition[] = [];
    const state = versionedAdapter(seen);

    const { result } = renderHook(() =>
      useSavedFilters(state, "patient-2", () => undefined),
    );
    await waitFor(() => expect(result.current).toBeTruthy());

    act(() => {
      result.current.reset();
    });

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({ kind: "ifMatch", version: '"v0"' });
  });
});
