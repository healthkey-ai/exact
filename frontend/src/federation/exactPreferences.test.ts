// Preferences from EXACT's own store, and the composition that gets them
// there while favourites and registration interest still come from PROMOP.
//
// What is worth testing here is not the four verbs. It is that this store
// asks for the row WITHOUT naming a patient, that it does not claim a
// conditional-write ability it does not have, and that composing it over the
// PROMOP adapter replaces exactly the preference half and nothing else.

import { describe, expect, it, vi } from "vitest";
import type { AxiosInstance } from "axios";

import { composeState, createExactPreferences } from "./state";
import type { TrialStateAdapter } from "./state";

/** CALLABLE, like the real thing.
 *
 *  A plain object would pass every test here and hide the one claim this
 *  file is meant to defend: an `AxiosInstance` is itself a function, which
 *  is why the store takes a getter rather than a union of "instance or
 *  getter" — `typeof client === "function"` would narrow nothing. A fake
 *  that is not callable cannot tell that story wrong or right. */
function fakeClient(row: Record<string, unknown> = {}) {
  const callable = () => {
    throw new Error("called as a function");
  };
  const client = Object.assign(callable, {
    get: vi.fn(async () => ({ data: row, headers: {} })),
    post: vi.fn(async () => ({ data: {}, headers: {} })),
  });
  return client as typeof client & AxiosInstance;
}

describe("the store EXACT keeps itself", () => {
  it("asks for the row without naming a patient", async () => {
    // The whole reason this store exists. PROMOP's adapter sends
    // `?person_id=`, which arrives from the host bound to nothing; here the
    // row is found by the identity in the token, so there is no id to send
    // and no id to get wrong.
    const client = fakeClient({ preferences: { phase: "II" } });

    await createExactPreferences(() => client).getPreferences();

    expect(client.get).toHaveBeenCalledWith("/user-state/trial-search-preferences/");
  });

  it("reads an absent or malformed payload as no filters", async () => {
    // The column is opaque by design, so a row written by another client is
    // this page's problem not to crash on, not its problem to interpret.
    for (const row of [{}, { preferences: null }, { preferences: ["phase"] }]) {
      const store = createExactPreferences(() => fakeClient(row));

      expect(await store.getPreferences()).toEqual({});
    }
  });

  it("sends the filters under the key the column is named by", async () => {
    // The one verb that writes the reader's filters, and it had no test:
    // posting the filters bare passed everything here. DRF ignores unknown
    // top-level keys, so the failure mode is a 200 that stores nothing —
    // which is the promop#1602 shape this store exists to avoid repeating.
    const client = fakeClient();

    await createExactPreferences(() => client).savePreferences({ phase: "II" });

    expect(client.post).toHaveBeenCalledWith(
      "/user-state/trial-search-preferences/",
      { preferences: { phase: "II" } },
    );
  });

  it("resets through its own route rather than saving nothing", async () => {
    // `savePreferences({})` is a partial write and would leave the filters
    // alone; the endpoint also keeps what is not a filter, which is why the
    // two are different calls on both sides of the migration.
    const client = fakeClient();

    await createExactPreferences(() => client).resetPreferences();

    expect(client.post).toHaveBeenCalledWith(
      "/user-state/trial-search-preferences/reset/",
      {},
    );
  });

  it("offers no conditional write, because there is none", async () => {
    // Last writer wins, deliberately. Claiming `preferenceVersioning` would
    // make the caller send a precondition against a tag this store never
    // issues — which is how promop#1602 became a write no browser could
    // complete.
    expect(createExactPreferences(() => fakeClient()).preferenceVersioning).toBeUndefined();
  });

  it("records the wizard answer without touching the filters", async () => {
    // Two writes, not one: the weights go through `savePreferences` so the
    // caller's belief of what is stored stays right.
    const client = fakeClient();

    await createExactPreferences(() => client).weightsWizard!.record();

    expect(client.post).toHaveBeenCalledWith(
      "/user-state/trial-search-preferences/",
      { weights_wizard_offered: true },
    );
  });

  it("treats a server that does not know the column as already asked", async () => {
    // The third answer, and the one that matters: asking on every visit
    // forever is worse than not asking, and only the row can say whether the
    // server kept the flag.
    const store = createExactPreferences(() => fakeClient({ preferences: {} }));

    expect(await store.weightsWizard!.wasOffered()).toBe(true);
  });

  it("reads the client at call time, not at construction", async () => {
    // The adapter must survive a token refresh without being rebuilt —
    // rebuilding drops the writes already queued against it — while the
    // client it writes through is rebuilt on exactly that event.
    let current = fakeClient({ preferences: { phase: "I" } });
    const store = createExactPreferences(() => current);
    await store.getPreferences();

    const refreshed = fakeClient({ preferences: { phase: "III" } });
    current = refreshed;

    expect(await store.getPreferences()).toEqual({ phase: "III" });
    expect(refreshed.get).toHaveBeenCalled();
  });
});

describe("composing the two stores", () => {
  // Rebuilt per test. Shared `vi.fn()`s made the third assertion below
  // order-dependent on the first two: it would have broken for an unrelated
  // reason the day one of them touched the wizard.
  const freshBase = () => ({
    listFavoriteIds: vi.fn(async () => ["1"]),
    setFavorite: vi.fn(async () => undefined),
    listRegisteredIds: vi.fn(async () => ["2"]),
    setRegistered: vi.fn(async () => undefined),
    listAdvancedEnrollments: vi.fn(async () => ({})),
    getPreferences: vi.fn(async () => ({ phase: "PROMOP" })),
    savePreferences: vi.fn(async () => undefined),
    resetPreferences: vi.fn(async () => undefined),
    weightsWizard: { wasOffered: vi.fn(async () => false), record: vi.fn(async () => undefined) },
    preferenceVersioning: { read: vi.fn(), write: vi.fn(), clear: vi.fn() },
  }) as unknown as TrialStateAdapter;

  it("takes preferences from EXACT and leaves the rest with PROMOP", async () => {
    const client = fakeClient({ preferences: { phase: "EXACT" } });

    const composed = composeState(freshBase(), createExactPreferences(() => client));

    expect(await composed.getPreferences()).toEqual({ phase: "EXACT" });
    expect(await composed.listFavoriteIds()).toEqual(["1"]);
    expect(await composed.listRegisteredIds()).toEqual(["2"]);
  });

  it("does not carry PROMOP's conditional write over to a store without one", async () => {
    // The spread would. A precondition against a tag the new store never
    // issues is a write that cannot succeed, and the caller has no way to
    // know the two halves came from different services.
    const composed = composeState(freshBase(), createExactPreferences(() => fakeClient()));

    expect(composed.preferenceVersioning).toBeUndefined();
  });

  it("takes the wizard from the same side as the filters", async () => {
    // They are one row on one server. Reading the flag from PROMOP while
    // writing the weights to EXACT would offer the wizard to a reader who
    // had already answered it, or hide it from one who had not.
    const base = freshBase();

    const composed = composeState(
      base,
      createExactPreferences(() => fakeClient({ weights_wizard_offered: true })),
    );

    expect(await composed.weightsWizard!.wasOffered()).toBe(true);
    expect(base.weightsWizard!.wasOffered).not.toHaveBeenCalled();
  });
});
