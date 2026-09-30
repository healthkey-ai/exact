// The one read the guard rests on, and the two ways it was got wrong.
//
// `adapterPreferences` caches what the server holds, and under an endpoint
// that REPLACES the row that cache is not an optimisation — it is the merge
// base every later save is built on. So it has to belong to somebody, and
// the question this file pins is: WHO, for the read that happens at mount?
//
// Two review rounds answered it wrongly in opposite directions, and both
// answers passed the suite as it then stood:
//
//   round 1 — stamped from the cached fingerprint BEFORE the read. At mount
//     that cache has never been written: it is filled in by the client's
//     own interceptor, and this read is the first request to fetch a token.
//     So the cache went unstamped, `sameIdentity(undefined, anyone)` is
//     true, and the guard was off for a reader who loads their filters and
//     never saves — which is most readers.
//
//   round 2 — stamped from the cached fingerprint AFTER the read. By then
//     it names whoever the host has swapped in since. A read issued as one
//     reader and resolving after a swap stamped the cache with TWO while
//     holding ONE's row; every later check then agreed and the merge went
//     ahead.
//
// The cases below are the second one and its neighbour, kept because they
// fail against both wrong answers and because neither was caught by the
// generated interleavings in `preferences.identity.property.test.ts` — that
// harness always has an answer to give, and these are about not having one.
// Property test for the general invariant, these for the premise.
import { describe, expect, it } from "vitest";

import { adapterPreferences } from "./preferences";
import type { FilterState } from "./types";

/** A server with one row per reader, attributing each request to whoever is
 *  signed in AT THE MOMENT IT IS MADE — which is what the backend does, and
 *  what a fake answering one row to everybody cannot express. An earlier
 *  regression test for this very bug passed because its fake could not tell
 *  two readers apart. */
function twoRowServer(whoNow: () => string) {
  const rows: Record<string, Record<string, unknown>> = {};
  let gate: (() => void) | null = null;
  let hold = false;
  const wait = async () => {
    if (!hold) return;
    await new Promise<void>((r) => {
      gate = r;
    });
  };
  return {
    rows,
    holdNext: () => {
      hold = true;
    },
    release: () => {
      hold = false;
      gate?.();
      gate = null;
    },
    methods: {
      getPreferences: async () => {
        const at = whoNow();
        await wait();
        return (rows[at] ?? {}) as never;
      },
      savePreferences: async (f: FilterState) => {
        rows[whoNow()] = { ...(f as object) };
      },
      resetPreferences: async () => {
        rows[whoNow()] = {};
      },
    },
  };
}

describe("a cached row belongs to the reader its read went out for", () => {
  it("does not hand a mount read to whoever is signed in when it lands", async () => {
    // The host supplies only the CACHED reader, not the asked-afresh one —
    // permitted by the prop contract, and the shape in which there is no
    // answer available at issue time. The read therefore cannot be
    // attributed, and the rule is that an unattributable cache is not one
    // the next reader may inherit.
    let who: string | undefined = undefined;
    const s = twoRowServer(() => who ?? "one");
    s.rows["one"] = { searchTitle: "USER-1-ONLY", country: "US" };
    s.rows["sub:iss|two"] = { country: "CA" };
    const t = adapterPreferences(s.methods, () => who);

    s.holdNext();
    const reading = t.get();
    await Promise.resolve();
    // The swap, with nothing in the React tree told about it: one of the
    // four shapes #583 measured.
    who = "sub:iss|two";
    s.release();
    await reading;

    // The read is dropped rather than adopted, so nothing was seeded and
    // the arriving reader's save takes the re-read path — which reads THEIR
    // row and merges into that.
    await t.save({ distance: 100 } as FilterState);
    expect(s.rows["sub:iss|two"]).not.toHaveProperty("searchTitle", "USER-1-ONLY");
    // Their own row survived intact. Under a wholesale replace the loss
    // would not have been "an extra filter" but everything they had.
    expect(s.rows["sub:iss|two"]).toEqual({ country: "CA", distance: 100 });
    // And the departed reader's row was not touched either.
    expect(s.rows["one"]).toEqual({ searchTitle: "USER-1-ONLY", country: "US" });
  });

  it("does not refuse a host that has no credentials at all", async () => {
    // The other direction, and the reason `cacheStillBelongsTo` is not just
    // `believedFor === current`. A host with no identity mechanism answers
    // unknown for ever; refusing there would break the deployment this
    // guard can help least — the local stand, and anything behind a gateway
    // that injects the header.
    const s = twoRowServer(() => "one");
    s.rows["one"] = { searchTitle: "kept", country: "US" };
    const t = adapterPreferences(s.methods);

    await t.get();
    await t.save({ distance: 100 } as FilterState);
    expect(s.rows["one"]).toMatchObject({ searchTitle: "kept", distance: 100 });
  });

  it("stamps from the ask, so a token that merely rotated is the same reader", async () => {
    // The failure mode of over-tightening: Firebase hands out a fresh JWT
    // about once an hour, and comparing credentials as strings turns that
    // into a dropped edit mid-debounce.
    let rotations = 0;
    const s = twoRowServer(() => "sub:iss|one");
    s.rows["sub:iss|one"] = { searchTitle: "kept" };
    const t = adapterPreferences(
      s.methods,
      () => "sub:iss|one",
      // Every ask mints a fresh credential, as Firebase does. The
      // FINGERPRINT does not move, and nothing here looks at the token
      // itself — which is the whole reason `fingerprintOf` exists.
      async () => {
        rotations += 1;
        return "sub:iss|one";
      },
    );

    await t.get();
    await t.save({ distance: 100 } as FilterState);
    // Asked more than once — at issue, at landing, and before the write —
    // so this really is several different credentials, and not one of them
    // cost the reader anything.
    expect(rotations).toBeGreaterThan(1);
    expect(s.rows["sub:iss|one"]).toMatchObject({ searchTitle: "kept", distance: 100 });
  });
});
