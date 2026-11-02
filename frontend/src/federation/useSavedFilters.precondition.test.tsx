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

describe("useSavedFilters().settle", () => {
  /** An adapter whose writes fail on demand. */
  function flakyAdapter(fails: () => boolean): TrialStateAdapter {
    return {
      listFavoriteIds: async () => [],
      setFavorite: async () => undefined,
      listRegisteredIds: async () => [],
      setRegistered: async () => undefined,
      listAdvancedEnrollments: async () => ({}),
      getPreferences: async () => ({}) as never,
      savePreferences: async () => {
        if (fails()) throw new Error("503");
      },
      resetPreferences: async () => undefined,
      getWritableFields: async () => ({}) as never,
    } as unknown as TrialStateAdapter;
  }

  it("says false when the write it waited for did not land", async () => {
    const state = flakyAdapter(() => true);
    const { result } = renderHook(() =>
      useSavedFilters(state, "patient-1", () => undefined),
    );
    await waitFor(() => expect(result.current).toBeTruthy());

    let verdict: boolean | undefined;
    await act(async () => {
      result.current.persist({ distance: 50 } as never);
      verdict = await result.current.settle();
    });
    expect(verdict).toBe(false);
  });

  it("does not let a later success in the same drain mask an earlier failure", async () => {
    // The scope is the DRAIN, and a drain is not one write: `settled()` keeps
    // waiting while the queue refills. With a success clearing the verdict, a
    // write landing after an earlier one had failed answered `true`, and the
    // wizard would record "this reader has answered" over a ranking that was
    // never stored.
    //
    // `reset()` rather than a second `persist`, because only an enqueued
    // write joins a drain already under way — a `persist` sits on the
    // debounce and is not part of it. Reset is the caller that can genuinely
    // land behind a failing save.
    // Saves always fail; `resetPreferences` always succeeds.
    const state = flakyAdapter(() => true);
    const { result } = renderHook(() =>
      useSavedFilters(state, "patient-1", () => undefined),
    );
    await waitFor(() => expect(result.current).toBeTruthy());

    let verdict: boolean | undefined;
    await act(async () => {
      result.current.persist({ distance: 50 } as never);
      const settled = result.current.settle();
      result.current.reset();
      verdict = await settled;
    });
    expect(verdict).toBe(false);
  });

  it("forgets a failure that happened before it was called", async () => {
    // Otherwise one failed save early in the session would refuse the wizard
    // its flag for the rest of the page's life.
    let attempt = 0;
    const state = flakyAdapter(() => (attempt += 1) === 1);
    const { result } = renderHook(() =>
      useSavedFilters(state, "patient-1", () => undefined),
    );
    await waitFor(() => expect(result.current).toBeTruthy());

    await act(async () => {
      result.current.persist({ distance: 50 } as never);
      await result.current.settle();
    });

    let verdict: boolean | undefined;
    await act(async () => {
      result.current.persist({ distance: 60 } as never);
      verdict = await result.current.settle();
    });
    expect(verdict).toBe(true);
  });
});
