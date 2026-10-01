// One reader's saved filters must never reach another reader's row.
//
// WHY THIS FILE EXISTS RATHER THAN A THIRD REVIEW ROUND. Two rounds of
// review found the same defect in the same function with two different
// spellings: the cache that says "the server holds this" was stamped with
// the wrong reader, first by reading the fingerprint too early and then by
// reading it too late. Each round closed one spelling. Both shared a
// premise — that the identity of a request can be recovered afterwards by
// inspecting a cache some OTHER request may have moved — and a third round
// would have found a third spelling of it.
//
// So the invariant is written down and generated against instead. Every
// example below is produced from a seed; a failure prints the seed and the
// exact op sequence, which is the whole point of keeping the harness here
// rather than throwing it away with the review.
//
// THE TWO INVARIANTS
//
//   1. No value in reader B's row originated from a read of reader A's row.
//      This is the leak in plain terms. It covers the merge base (#603),
//      because a save composed against somebody else's cache carries their
//      values, and the endpoint REPLACES the row rather than merging.
//
//   2. `seeded` implies `believedFor` is stamped. Not observable from
//      outside, so it is asserted through its consequence: an unstamped
//      cache switches the guard off for good — `sameIdentity(undefined,
//      anyone)` is true — so the sequences end with one more reader and one
//      more save, and invariant 1 is checked again after it. A transport
//      that reached the unstamped state passes the first check and fails
//      the second.
//
// WHAT THE FAKE SERVER MODELS, and why each part is load-bearing:
//
//   * A request is attributed to whoever the credential names AT THE MOMENT
//     IT IS MADE, not when it resolves. That is what EXACT's backend does,
//     and a fake that answered one row to everybody cannot tell a working
//     guard from a broken one — an earlier regression test for this very
//     bug passed for exactly that reason.
//   * A write REPLACES the row. PROMOP's endpoint has no server-side merge,
//     which is why a wrong merge base destroys the victim's other filters
//     rather than adding to them.
//   * Reading a token refreshes the cached fingerprint, because in the real
//     bridge that cache is written by the client's interceptor. This is the
//     thing both defects turned on: between a swap and the next request the
//     cache still names the reader who has gone.
import { describe, expect, it } from "vitest";

import { adapterPreferences } from "./preferences";
import type { Precondition, VersionedPreferences } from "./state";
import type { FilterState } from "./types";

/** Reproducible randomness. A property test whose failures cannot be
 *  replayed is a flaky test; the seed is printed with every failure. */
const rng = (seed: number) => {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;
    s >>>= 0;
    return s / 0x100000000;
  };
};

const READERS = ["sub:iss|one", "sub:iss|two", "sub:iss|three"] as const;
/** The value that can only have come from this reader's row. */
const markerOf = (who: string) => `ONLY-${who}`;

interface Server {
  rows: Record<string, Record<string, unknown>>;
  /** Whoever the host has signed in right now. */
  signedIn: string;
  /** What a synchronous reading of the credential would say — stale until
   *  something fetches a token, exactly like the bridge's `identityRef`. */
  cached: string | undefined;
  /** Arms a one-shot rejection of the next ask. The bridge's
   *  `credentialIdentityNow` awaits the host's UNWRAPPED `getToken`, so a
   *  failed refresh, a network blip or a sign-out in flight propagates
   *  straight out of it. Modelled because leaving it out is exactly how a
   *  P1 got past this harness: the `catch` was unreachable here, so what
   *  it recorded could be arbitrary garbage with every test still green. */
  askFailsNext: boolean;
}

const newServer = (): Server => {
  const rows: Record<string, Record<string, unknown>> = {};
  for (const who of READERS) rows[who] = { marker: markerOf(who) };
  return { rows, signedIn: READERS[0], cached: undefined, askFailsNext: false };
};

/** Fetching a token, which is also what refreshes the cached reading. */
const fetchToken = (server: Server) => {
  server.cached = server.signedIn;
  return server.signedIn;
};

const strip = (row: Record<string, unknown>): FilterState => {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(row)) if (v !== undefined) out[k] = v;
  return out as FilterState;
};

/** The adapter methods, in both the shapes production has. */
const methodsFor = (server: Server, versioned: boolean) => {
  const tags: Record<string, string | null> = {};
  for (const who of READERS) tags[who] = `"v0-${who}"`;
  // Attributed when the call is ENTERED, like a request leaving with a
  // header on it. Everything after the first await belongs to whoever this
  // says, however long the round trip takes.
  const attribute = () => fetchToken(server);
  const base = {
    getPreferences: async () => {
      const who = attribute();
      await Promise.resolve();
      return strip(server.rows[who]);
    },
    savePreferences: async (filters: FilterState) => {
      const who = attribute();
      await Promise.resolve();
      server.rows[who] = { ...(filters as object) };
    },
    resetPreferences: async () => {
      const who = attribute();
      await Promise.resolve();
      server.rows[who] = {};
    },
  };
  if (!versioned) return base;
  return {
    ...base,
    preferenceVersioning: {
      read: async (): Promise<VersionedPreferences> => {
        const who = attribute();
        await Promise.resolve();
        return { filters: strip(server.rows[who]) as never, version: tags[who] };
      },
      write: async (filters: FilterState, _p: Precondition): Promise<string | null> => {
        // Preconditions are exercised by `preferences.test.ts`; here there
        // is one writer per row, so every write is accepted and the subject
        // under test is attribution alone.
        const who = attribute();
        await Promise.resolve();
        server.rows[who] = { ...(filters as object) };
        tags[who] = `"v-${who}-${Date.now()}"`;
        return tags[who];
      },
      clear: async (_p: Precondition): Promise<string | null> => {
        const who = attribute();
        await Promise.resolve();
        server.rows[who] = {};
        tags[who] = `"vc-${who}"`;
        return tags[who];
      },
    },
  };
};

/** Every row that has LOST its own marker.
 *
 *  Invariant 4, and it is the one that gives the unguarded axis something
 *  to fail. `foreignMarkers` only sees a stranger's value ARRIVING; a save
 *  composed over an empty base wipes the victim's row and leaves no
 *  stranger's marker behind, so it slips past. It is also what a transport
 *  that never adopts its own reads looks like from outside — every save
 *  builds on `{}` and replaces the row with one field. That was a real
 *  defect once (an over-tightened unknown rule refused the reader their
 *  own row on a plain page load) and nothing here could have caught it.
 *
 *  A reset is the one legitimate way to lose a marker, so rows cleared by
 *  one are excused. */
const markersLost = (server: Server, cleared: Set<string>): string[] => {
  const gone: string[] = [];
  for (const who of READERS) {
    if (cleared.has(who)) continue;
    if (server.rows[who].marker !== markerOf(who)) {
      gone.push(`${who} lost its own row`);
    }
  }
  return gone;
};

/** Every marker that is in the wrong row. */
const foreignMarkers = (server: Server): string[] => {
  const wrong: string[] = [];
  for (const who of READERS) {
    for (const value of Object.values(server.rows[who])) {
      if (typeof value !== "string" || !value.startsWith("ONLY-")) continue;
      if (value !== markerOf(who)) wrong.push(`${who} holds ${value}`);
    }
  }
  return wrong;
};

type Op = "get" | "save" | "reset" | "swap" | "settle" | "askFails";

const OPS: Op[] = [
  "get",
  "save",
  "reset",
  "swap",
  "settle",
  "get",
  "save",
  "askFails",
];

const runSequence = async (seed: number, versioned: boolean, asked: boolean) => {
  const random = rng(seed);
  const server = newServer();
  const methods = methodsFor(server, versioned);
  const transport = adapterPreferences(
    methods as never,
    () => server.cached,
    // `asked: false` is a host that cannot be asked at all — no
    // `credentialIdentityNow`. THE GUARD IS OFF for it, by decision: see
    // `whoThisRequestIsFor`. What this axis pins is that being off is all
    // that happens — the transport still loads, merges and saves, and a
    // reader's own row is not damaged by their own use of it. An earlier
    // revision tried to protect this shape halfway and produced both a
    // leak and a panel that never loaded.
    asked
      ? // Asked afresh: fetches a token, which is what makes the cached
        // reading current — the bridge's `credentialIdentityNow` does
        // exactly this. Unless the host's `getToken` is having a bad
        // moment, in which case the rejection comes out of here.
        async () => {
          if (server.askFailsNext) {
            server.askFailsNext = false;
            throw new Error("token refresh failed");
          }
          return fetchToken(server);
        }
      : undefined,
  );

  const log: string[] = [];
  /** Invariant 3's violations: rows a read put in front of the wrong
   *  reader. Collected rather than thrown so one sequence reports all of
   *  them. */
  const handedOver: string[] = [];
  /** Readers whose row a reset was attributed to — see `markersLost`. */
  const cleared = new Set<string>();
  // Never awaited in lockstep: the defects both lived in the gap between a
  // request being issued and resolving, so the sequence deliberately leaves
  // work in flight and settles it later.
  const inFlight: Promise<unknown>[] = [];
  // SAVES AND RESETS ARE SERIALISED, because `PreferenceWriter` serialises
  // them and the transport is never called directly by anything else.
  //
  // Found the hard way: overlapping them is not a harsher test, it is a
  // different program. Two saves sharing one transport share `stored`, so
  // one refusing drops the cache out from under the other after it has
  // passed its own checks, and the second writes `merge({})` — a wiped row
  // with nobody's values in it. Real, but unreachable, and a fake that is
  // stricter than reality spends the same attention as one that is looser.
  // Reads stay concurrent: those genuinely overlap in the tree, which is
  // what `firstRead` exists for.
  let writes: Promise<unknown> = Promise.resolve();
  const serialise = (start: () => Promise<unknown>) => {
    const next = writes.then(start, start);
    writes = next.catch(() => undefined);
    return next;
  };
  const swallow = (p: Promise<unknown>) => {
    // A refusal is a correct outcome here — the transport declining to write
    // across a swap is the behaviour under test — so rejections are recorded
    // rather than thrown.
    inFlight.push(p.catch(() => undefined));
  };

  const steps = 4 + Math.floor(random() * 9);
  for (let i = 0; i < steps; i += 1) {
    const op = OPS[Math.floor(random() * OPS.length)];
    log.push(`${op}(signedIn=${server.signedIn})`);
    if (op === "get") {
      // INVARIANT 3 — what a read HANDS BACK, not only what it caches.
      //
      // The caller paints this into the filter panel (`hooks.ts`, the load
      // effect), so a read that resolves after a swap holding the departed
      // reader's row draws their saved search in front of the arriving
      // one — visible, and the arriving reader's next keystroke sends the
      // whole panel back. Judged against whoever is signed in AT THE
      // MOMENT IT RESOLVES, because that is who is looking at it.
      //
      // Added after the first version of this file asserted only on server
      // rows: six of the guards below could be deleted with every test
      // still green, because the write path caught what the read path let
      // through one caller later.
      swallow(
        transport.get().then((row) => {
          const at = server.signedIn;
          for (const value of Object.values(row as Record<string, unknown>)) {
            if (typeof value !== "string" || !value.startsWith("ONLY-")) continue;
            if (value !== markerOf(at)) handedOver.push(`${at} was shown ${value}`);
          }
        }),
      );
    }
    else if (op === "save") {
      // The reader types something of their own. What they must never send
      // is somebody else's marker, and they can only be holding one if the
      // transport handed it to them.
      const who = server.signedIn;
      swallow(serialise(() => transport.save({ searchTitle: `typed-by-${who}` } as FilterState)));
    } else if (op === "reset") {
      // Whoever the clear is attributed to legitimately loses their
      // marker; invariant 4 excuses exactly those.
      cleared.add(server.signedIn);
      swallow(serialise(() => transport.reset()));
    }
    else if (op === "swap") {
      // Only one reader ever signs in on the unguarded shape. Swapping
      // there would assert isolation that is explicitly not promised, and
      // a test that asserts an unpromised thing either fails or, worse,
      // passes for a reason nobody chose.
      if (!asked) continue;
      server.signedIn = READERS[Math.floor(random() * READERS.length)];
      // Deliberately NOT refreshing `server.cached`: an unsignalled swap is
      // invisible until something next fetches a token, and that window is
      // where both defects lived.
    } else if (op === "askFails") {
      server.askFailsNext = true;
    } else {
      await Promise.all(inFlight.splice(0));
      // A few turns of the microtask queue, so anything chained settles.
      for (let t = 0; t < 4; t += 1) await Promise.resolve();
    }
  }
  await Promise.all(inFlight.splice(0));
  for (let t = 0; t < 8; t += 1) await Promise.resolve();

  const afterTheSequence = foreignMarkers(server);

  // INVARIANT 2, through its consequence. A transport left `seeded` with no
  // stamp waves every later reader through, and nothing above has to notice
  // — so one more reader arrives and writes, and invariant 1 is asked again.
  if (asked) {
    server.signedIn =
      READERS[(READERS.indexOf(server.signedIn as never) + 1) % READERS.length];
    log.push(`final-swap(signedIn=${server.signedIn})`);
  }
  await serialise(() => transport.save({ searchTitle: "last-word" } as FilterState)).catch(
    () => undefined,
  );
  for (let t = 0; t < 8; t += 1) await Promise.resolve();

  return {
    server,
    log,
    afterTheSequence,
    handedOver,
    lost: markersLost(server, cleared),
    afterOneMore: foreignMarkers(server),
  };
};

describe("no reader's saved filters reach another reader's row", () => {
  const shapes: Array<[boolean, boolean, string]> = [
    [true, true, "the versioned adapter, host answers afresh"],
    [false, true, "no versioning, host answers afresh"],
    [true, false, "the versioned adapter, a host that cannot be asked"],
    [false, false, "no versioning, a host that cannot be asked"],
  ];
  for (const [versioned, asked, what] of shapes) {
    it(`holds over generated interleavings — ${what}`, async () => {
      const failures: string[] = [];
      for (let seed = 1; seed <= 300; seed += 1) {
        const { log, afterTheSequence, handedOver, lost, afterOneMore } = await runSequence(
          seed,
          versioned,
          asked,
        );
        if (
          afterTheSequence.length ||
          afterOneMore.length ||
          handedOver.length ||
          lost.length
        ) {
          failures.push(
            [
              `seed ${seed}`,
              `  ops: ${log.join(" → ")}`,
              handedOver.length ? `  a read handed over: ${handedOver.join("; ")}` : "",
              lost.length ? `  a row was wiped: ${lost.join("; ")}` : "",
              afterTheSequence.length ? `  after the sequence: ${afterTheSequence.join("; ")}` : "",
              afterOneMore.length ? `  after one more reader: ${afterOneMore.join("; ")}` : "",
            ]
              .filter(Boolean)
              .join("\n"),
          );
        }
        // Enough detail to replay, and short enough to read. Ten is plenty
        // to see the shape; the seed reproduces the rest.
        if (failures.length >= 10) break;
      }
      expect(failures.join("\n\n")).toBe("");
    });
  }

  it("still lets one reader save their own filters, which is what it must not break", async () => {
    // The guard's failure mode in the other direction: refusing everything
    // also satisfies invariant 1, and would be worthless. This pins that a
    // single reader with no swap at all still gets their edit stored.
    const server = newServer();
    const transport = adapterPreferences(
      methodsFor(server, true) as never,
      () => server.cached,
      async () => fetchToken(server),
    );
    await transport.get();
    await transport.save({ searchTitle: "mine" } as FilterState);
    expect(server.rows[READERS[0]]).toMatchObject({ searchTitle: "mine" });
    // And the rest of their row survived, because the merge base was theirs.
    expect(server.rows[READERS[0]].marker).toBe(markerOf(READERS[0]));
  });
});
