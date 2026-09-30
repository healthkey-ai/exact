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

import { PreconditionFailed } from "./state";
import type { Precondition, VersionedPreferences } from "./state";

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
    // THE SUPPORTED CONTRACT: both halves of the reader. The cached one
    // is still unknown when the mount read is issued — it is written by
    // the client's interceptor and this read is the first request to
    // fetch a token — so the issue-time answer has to come from the ask,
    // and it does.
    let who = "one";
    // What a synchronous reading would say: nothing, until a request has
    // fetched a token. That is the state the mount read starts in.
    let cached: string | undefined = undefined;
    const s = twoRowServer(() => who);
    s.rows["one"] = { searchTitle: "USER-1-ONLY", country: "US" };
    s.rows["sub:iss|two"] = { country: "CA" };
    const t = adapterPreferences(
      s.methods,
      () => cached,
      async () => {
        cached = who;
        return who;
      },
    );

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

// ASKING COSTS A ROUND TRIP, AND A ROUND TRIP IS A WINDOW.
//
// The stamping above is bought with `credentialIdentityNow`, which fetches
// a token. Every one of those asks is an await, and every await added in
// front of a request is a gap something else can land in. Two did, both
// found by review after the stamping itself was right, and neither is
// about identity at all — they are about where the await was put.
describe("an await added in front of a write is a window", () => {
  /** A versioned adapter whose clear bumps the tag, so a save that goes
   *  out after a reset can be seen to have quoted the NEW tag — which is
   *  what makes the lost clear silent rather than a 412. */
  const versionedRow = () => {
    let row: Record<string, unknown> = {};
    let tag: string | null = "\"v0\"";
    let refuseOnce = false;
    const writes: Array<Record<string, unknown>> = [];
    return {
      writes,
      seen: () => ({ ...row }),
      /** Refuse the next write with a 412, as a second tab would. */
      contend: () => {
        refuseOnce = true;
      },
      put: (next: Record<string, unknown>) => {
        row = { ...next };
      },
      methods: {
        getPreferences: async () => ({ ...row }) as never,
        savePreferences: async () => {},
        resetPreferences: async () => {},
        preferenceVersioning: {
          read: async (): Promise<VersionedPreferences> =>
            ({ filters: { ...row } as never, version: tag }),
          write: async (filters: Record<string, unknown>, _p: Precondition) => {
            if (refuseOnce) {
              refuseOnce = false;
              throw new PreconditionFailed(tag);
            }
            writes.push({ ...filters });
            row = { ...filters };
            tag = `"w${writes.length}"`;
            return tag;
          },
          clear: async (_p: Precondition) => {
            row = {};
            tag = `"c"`;
            return tag;
          },
        },
      },
    };
  };

  it("does not let a Reset landing inside the ask bring the filters back", async () => {
    // Reset is the newer intent. The save was composed before it; sending
    // that payload afterwards quotes the tag Reset installed, so the
    // precondition PASSES and everything the reader cleared reappears —
    // no 412, nothing on `onError`, and the reader watches their filters
    // come back by themselves.
    //
    // The generation check that exists for this sits AFTER the write,
    // which is too late once there is an await in front of it.
    const a = versionedRow();
    a.put({ country: "US", sponsor: "Acme" });
    // Fired from INSIDE the ask, which is the only way to land the Reset
    // in that window deterministically. Disarmed as it fires, so the
    // Reset's own ask answers immediately instead of deadlocking.
    let duringTheAsk: null | (() => Promise<void>) = null;
    // `save` asks twice before it writes: once on entry, and once
    // immediately before the write. This case is the SECOND; the case
    // below is the first, which an earlier version of this comment
    // asserted was already covered. It was not — that was reasoning, not
    // measurement, and a reviewer measured it.
    let skip = 0;
    const t = adapterPreferences(
      a.methods as never,
      () => "sub:iss|one",
      async () => {
        if (duringTheAsk && skip > 0) {
          skip -= 1;
        } else if (duringTheAsk) {
          const hook = duringTheAsk;
          duringTheAsk = null;
          await hook();
        }
        return "sub:iss|one";
      },
    );

    await t.get();
    // After `get` the cache is seeded and the tag is known, so the first
    // ask `save` makes is the one immediately before the write.
    skip = 1;
    duringTheAsk = () => t.reset();
    await t.save({ distance: 100 } as FilterState);

    expect(a.seen()).toEqual({});
    expect(a.writes).toEqual([]);
  });

  it("does not report an unhandled rejection when the read fails first", async () => {
    // The request is started BESIDE the ask, on purpose — waiting for the
    // ask first let another writer's flush overtake it. The cost is that
    // between starting and awaiting it there is a stretch with no handler
    // attached, and a rejection there is reported as unhandled even though
    // the caller does catch. vitest fails the run on one, which is what
    // makes this assertable at all.
    let letTheAskFinish!: () => void;
    const askHeld = new Promise<void>((r) => {
      letTheAskFinish = r;
    });
    const t = adapterPreferences(
      {
        getPreferences: async () => {
          throw new Error("preferences service is down");
        },
        savePreferences: async () => {},
        resetPreferences: async () => {},
      } as never,
      () => "sub:iss|one",
      async () => {
        await askHeld;
        return "sub:iss|one";
      },
    );

    const reading = t.get().catch((e: Error) => e.message);
    // Long enough for the read's rejection to be noticed with nothing
    // listening, if nothing is.
    await new Promise((r) => setTimeout(r, 20));
    letTheAskFinish();
    // The failure still reaches the caller — acknowledged is not swallowed.
    expect(await reading).toBe("preferences service is down");
  });

  it("does not let a Reset landing inside the RETRY's ask bring them back", async () => {
    // The same window one step further along, and the path least walked:
    // the first write is refused by another tab, the transport re-reads,
    // re-applies the reader's edit — and asks once more before sending.
    // A Reset in THAT ask has the same consequence and needed its own
    // guard, which symmetry alone would not have proved.
    const a = versionedRow();
    a.put({ country: "US", sponsor: "Acme" });
    let duringTheAsk: null | (() => Promise<void>) = null;
    // Entry, before the first write, before the retry write.
    let skip = 0;
    const t = adapterPreferences(
      a.methods as never,
      () => "sub:iss|one",
      async () => {
        if (duringTheAsk && skip > 0) {
          skip -= 1;
        } else if (duringTheAsk) {
          const hook = duringTheAsk;
          duringTheAsk = null;
          await hook();
        }
        return "sub:iss|one";
      },
    );

    await t.get();
    a.contend();
    skip = 2;
    duringTheAsk = () => t.reset();
    await t.save({ distance: 100 } as FilterState);

    expect(a.seen()).toEqual({});
    expect(a.writes).toEqual([]);
  });

  it("does not let a Reset landing inside the FIRST ask bring them back", async () => {
    // The one I claimed did not need a test. `save` asks on entry, long
    // before it writes — but every generation it checks afterwards is
    // captured AFTER that ask, so a Reset landing inside it is already
    // folded into what those checks compare. They agree, the write quotes
    // the tag Reset installed, the precondition passes, and the reader
    // watches their cleared filters come back.
    //
    // The fix is a generation taken before anything is awaited at all.
    const a = versionedRow();
    a.put({ country: "US", sponsor: "Acme" });
    let duringTheAsk: null | (() => Promise<void>) = null;
    const t = adapterPreferences(
      a.methods as never,
      () => "sub:iss|one",
      async () => {
        if (duringTheAsk) {
          const hook = duringTheAsk;
          duringTheAsk = null;
          await hook();
        }
        return "sub:iss|one";
      },
    );

    await t.get();
    // No skip: the very first ask `save` makes.
    duringTheAsk = () => t.reset();
    await t.save({ distance: 100 } as FilterState);

    expect(a.seen()).toEqual({});
    expect(a.writes).toEqual([]);
  });
});
