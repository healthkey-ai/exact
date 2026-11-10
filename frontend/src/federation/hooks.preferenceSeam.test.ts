// The seam between the adapter and the preference transport.
//
// It is one object literal, and it fails silently when it is wrong: the
// transport keeps passing its own tests while the application writes
// unconditionally, because `adapterPreferences` decides whether it may send
// a precondition by asking whether `preferenceVersioning` is there. That is
// not a hypothetical — `preferenceVersioning` was dropped here in the first
// cut of #494 and only a review caught it.

import { describe, expect, it } from "vitest";

import { preferenceMethodsThrough } from "./hooks";
import type { Precondition, TrialStateAdapter } from "./state";

function adapter(withVersioning: boolean, label: string): TrialStateAdapter {
  const base = {
    listFavoriteIds: async () => [],
    setFavorite: async () => undefined,
    listRegisteredIds: async () => [],
    setRegistered: async () => undefined,
    listAdvancedEnrollments: async () => ({}),
    getPreferences: async () => ({ from: label }) as never,
    savePreferences: async () => undefined,
    resetPreferences: async () => undefined,
    getWritableFields: async () => ({}) as never,
  } as unknown as TrialStateAdapter;
  if (!withVersioning) return base;
  return {
    ...base,
    preferenceVersioning: {
      read: async () => ({ filters: { from: label } as never, version: `"${label}"` }),
      write: async (_f: never, _p: Precondition) => `"${label}-written"`,
      clear: async (_p: Precondition) => `"${label}-cleared"`,
    },
  };
}

describe("the preference seam", () => {
  it("forwards versioning, so the precondition reaches production at all", () => {
    const a = adapter(true, "a");
    expect(preferenceMethodsThrough(a, () => a).preferenceVersioning).toBeDefined();
  });

  it("omits it for an adapter that cannot do conditional writes", () => {
    // ht-phr's and CB's adapters predate the precondition. Claiming it on
    // their behalf would make the transport send headers nobody honours and
    // treat the answers as version information.
    const a = adapter(false, "a");
    expect(preferenceMethodsThrough(a, () => a).preferenceVersioning).toBeUndefined();
  });

  it("re-resolves through `live` on every call, not through the captured adapter", () => {
    // The captured adapter may hold an expired client; that is the whole
    // reason this indirection exists. A wrapper that closed over `captured`
    // would write through it.
    const captured = adapter(true, "captured");
    let current = captured;
    const methods = preferenceMethodsThrough(captured, () => current);

    current = adapter(true, "refreshed");
    return Promise.all([
      expect(methods.getPreferences()).resolves.toEqual({ from: "refreshed" }),
      expect(methods.preferenceVersioning!.read()).resolves.toEqual({
        filters: { from: "refreshed" },
        version: '"refreshed"',
      }),
      expect(
        methods.preferenceVersioning!.write({} as never, { kind: "none" }),
      ).resolves.toBe('"refreshed-written"'),
    ]);
  });

  it("says 'cannot describe' rather than 'no row' when it falls back to a plain read", async () => {
    // The fallback knows the filters and nothing about the version. Claiming
    // `null` there asserts there is no row, which is a claim it cannot make
    // — and the one that makes the next write send `If-None-Match: *`.
    const captured = adapter(true, "captured");
    const plain = adapter(false, "plain");
    const methods = preferenceMethodsThrough(captured, () => plain);

    await expect(methods.preferenceVersioning!.read()).resolves.toEqual({
      filters: { from: "plain" },
      version: undefined,
    });
  });

  it("still writes when the live adapter turns out to have no versioning", async () => {
    // Degrades to the unconditional write rather than throwing at the
    // reader. It answers `null` — the contract's "cannot say" — which the
    // transport normalises to "unknown" on the way in, so the next save
    // reads rather than assuming there is no row.
    const captured = adapter(true, "captured");
    const plain = adapter(false, "plain");
    let saved = false;
    plain.savePreferences = async () => {
      saved = true;
    };
    const methods = preferenceMethodsThrough(captured, () => plain);

    const version = await methods.preferenceVersioning!.write({} as never, {
      kind: "ifMatch",
      version: '"whatever"',
    });

    expect(saved).toBe(true);
    expect(version).toBeNull();
  });
});
