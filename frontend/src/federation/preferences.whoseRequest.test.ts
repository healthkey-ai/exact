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
    // The other direction, and the reason `belongsTo` is `sameIdentity`
    // rather than `believedFor === current`. A host with no identity
    // mechanism answers unknown for ever; refusing there would break the
    // deployment this guard can help least — the local stand, and
    // anything behind a gateway
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
    const preconditions: Precondition[] = [];
    const writes: Array<Record<string, unknown>> = [];
    return {
      writes,
      preconditions,
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
          write: async (filters: Record<string, unknown>, p: Precondition) => {
            preconditions.push(p);
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

  it("does not stamp a cache with 'unknown' when the ask itself failed", async () => {
    // THE STATE THAT WAS NOT REPRESENTED, and the P1 that came of leaving
    // it out. "We asked and it threw" was recorded as `undefined`, which
    // is the same value as "this deployment has nobody to name" — and
    // `sameIdentity` matches that against everybody, by design, so hosts
    // without accounts keep working. One rejecting `getToken` therefore
    // stamped the cache with a value that fitted the NEXT reader too, for
    // the life of the transport.
    //
    // This case had no test at all before, which is why it shipped: the
    // `catch` could be made to return arbitrary garbage and 1214 tests
    // stayed green.
    const rows: Record<string, Record<string, unknown>> = {
      one: { searchTitle: "USER-1-ONLY", country: "US" },
      two: { country: "CA" },
    };
    let signedIn = "one";
    let cached: string | undefined = undefined;
    let failNext = true;
    const methods = {
      getPreferences: async () => {
        const at = signedIn;
        cached = at;
        return { ...rows[at] } as never;
      },
      savePreferences: async (f: FilterState) => {
        rows[signedIn] = { ...(f as object) };
      },
      resetPreferences: async () => {
        rows[signedIn] = {};
      },
    };
    const t = adapterPreferences(
      methods as never,
      () => cached,
      // The bridge's shape: `noteIdentity(await getToken())`. An unwrapped
      // host `getToken` that rejects propagates straight out of here — a
      // failed Firebase refresh, a network blip, a sign-out in flight.
      async () => {
        if (failNext) {
          failNext = false;
          throw new Error("token refresh failed");
        }
        cached = signedIn;
        return signedIn;
      },
    );

    await t.get();
    signedIn = "two";
    await t.save({ distance: 100 } as FilterState).catch(() => undefined);

    expect(rows["two"]).not.toHaveProperty("searchTitle", "USER-1-ONLY");
    // And it recovers by itself rather than wedging: the mismatch drops
    // the cache, the save re-reads, and that read's ask succeeds.
    expect(rows["two"]).toEqual({ country: "CA", distance: 100 });
    expect(rows["one"]).toEqual({ searchTitle: "USER-1-ONLY", country: "US" });
  });

  it("does not turn a conditional write unconditional when forget() lands in the ask", async () => {
    // `precondition()` reads `version` when it is CALLED. `forget()` sets
    // that to `undefined` and deliberately does not move `generation` — it
    // comes from the weights wizard's flag write, which is not serialised
    // behind the save queue. So a `precondition()` evaluated after the
    // awaited identity check answers `{ kind: "none" }`, and the write
    // goes out with no `If-Match` at all: no 412, no retry, another tab's
    // edit destroyed with nothing on `onError`.
    //
    // Before this branch there was no await between the refresh and the
    // write, so the window is one the identity checks opened. Captured
    // with the payload, the worst case is a stale tag, which 412s.
    const a = versionedRow();
    a.put({ country: "US" });
    let duringTheAsk: null | (() => void) = null;
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
          hook();
        }
        return "sub:iss|one";
      },
    );

    await t.get();
    // The ask immediately before the write, as in the Reset cases above.
    skip = 1;
    duringTheAsk = () => t.forget();
    await t.save({ distance: 100 } as FilterState);

    expect(a.preconditions).toHaveLength(1);
    expect(a.preconditions[0].kind).toBe("ifMatch");
  });

  it("stamps a Reset too, so an unseeded one cannot wave the next reader through", async () => {
    // `seeded` WITHOUT A STAMP switches the guard off for the life of the
    // transport, because `sameIdentity(undefined, anyone)` is true. Reset
    // is the one path that can reach that state: it claims `seeded` on its
    // way out, and before this branch it claimed it bare.
    //
    // The invariant — `seeded` implies `believedFor` is stamped — was
    // stated in the property harness and asserted by nothing: deleting the
    // stamp from `reset()` left all 1216 tests green. This is the example
    // the generated sequences could not produce, because a wiped row
    // carries no stranger's marker for them to notice.
    const rows: Record<string, Record<string, unknown>> = {
      one: { country: "US" },
      two: { country: "CA", sponsor: "Acme", distance: 25 },
    };
    let signedIn = "one";
    let cached: string | undefined = undefined;
    const methods = {
      getPreferences: async () => {
        cached = signedIn;
        return { ...rows[signedIn] } as never;
      },
      savePreferences: async (f: FilterState) => {
        cached = signedIn;
        rows[signedIn] = { ...(f as object) };
      },
      resetPreferences: async () => {
        cached = signedIn;
        rows[signedIn] = {};
      },
    };
    const t = adapterPreferences(
      methods as never,
      () => cached,
      async () => {
        cached = signedIn;
        return signedIn;
      },
    );

    // Reset before anything has seeded — the reader clears the panel on a
    // fresh mount.
    await t.reset();
    // The host swaps, with nothing in the tree told about it.
    signedIn = "two";
    // Reader two's first edit.
    await t.save({ distance: 100 } as FilterState).catch(() => undefined);

    // Unstamped, this wrote `{distance: 100}` over reader two's row and
    // took their country, sponsor and distance with it.
    expect(rows["two"]).toEqual({ country: "CA", sponsor: "Acme", distance: 25 });
  });

  it("asks WHO the clear is for before it issues one, and refuses a stranger's", async () => {
    // #613. Every other write on this transport compares the reader before
    // it sends; `reset` compared nobody, so a clear issued for one reader
    // emptied whichever row the credential named when it LANDED. The endpoint
    // REPLACES the row, so that is the victim's whole row and not an edit
    // they can make again.
    //
    // `askedBy` comes from the caller because it is the only place the answer
    // exists: everything this transport could compare against describes
    // whoever is signed in NOW, which is exactly the value in question. The
    // generated harness found the interleaving; this pins the mechanism, so
    // deleting the check is a failure with a name on it.
    let signedIn = "sub:iss|one";
    let asked = 0;
    const cleared: string[] = [];
    const t = adapterPreferences(
      {
        getPreferences: async () => ({ country: "US" }) as never,
        savePreferences: async () => {},
        resetPreferences: async () => {
          // Attributed when the call is ENTERED, like a request leaving with
          // a header on it.
          cleared.push(signedIn);
        },
      } as never,
      () => signedIn,
      async () => {
        asked += 1;
        return signedIn;
      },
    );

    await t.reset("sub:iss|one");
    // Asked, and the clear went out as the reader who asked.
    expect(asked).toBeGreaterThan(0);
    expect(cleared).toEqual(["sub:iss|one"]);

    // The account changes while the write is queued.
    signedIn = "sub:iss|two";
    await expect(t.reset("sub:iss|one")).rejects.toThrow(
      "the signed-in account changed while these filters were being reset",
    );
    // Nothing emptied.
    expect(cleared).toEqual(["sub:iss|one"]);
  });
});
