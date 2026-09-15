// The write queue is the point of this module, so it is what these test.
//
// Each case is a race that actually happens in the panel: two checkboxes in
// one frame, a Reset pressed while a save is on the wire, a navigation away
// mid-debounce. The look of the filter panel is covered elsewhere; this is
// the behaviour CB earned the hard way.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  FILTER_DEBOUNCE_MS,
  PreferenceWriter,
  adapterPreferences,
  localStoragePreferences,
  type PreferenceTransport,
} from "./preferences";
import { PreconditionFailed } from "./state";
import type { Precondition, VersionedPreferences } from "./state";
import type { FilterState } from "./types";

/** A transport that lets the test decide when each request completes. */
function controllable() {
  const calls: Array<{ kind: "save" | "reset"; value?: FilterState }> = [];
  const resolvers: Array<() => void> = [];
  const hold = () =>
    new Promise<void>((resolve) => {
      resolvers.push(resolve);
    });
  const transport: PreferenceTransport = {
    get: async () => ({}),
    save: (value) => {
      calls.push({ kind: "save", value });
      return hold();
    },
    reset: () => {
      calls.push({ kind: "reset" });
      return hold();
    },
  };
  return {
    transport,
    calls,
    /** Complete the oldest outstanding request. */
    settle: async () => {
      resolvers.shift()?.();
      await Promise.resolve();
      await Promise.resolve();
    },
    outstanding: () => resolvers.length,
  };
}

describe("PreferenceWriter", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("collapses rapid edits into one request", async () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    w.save({ country: "US" });
    w.save({ country: "US", distance: 50 });
    w.save({ country: "US", distance: 100 });
    expect(t.calls).toHaveLength(0);

    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    expect(t.calls).toEqual([
      { kind: "save", value: { country: "US", distance: 100 } },
    ]);
  });

  it("keeps one request in flight and runs the next after it", async () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    expect(t.outstanding()).toBe(1);

    w.save({ distance: 2 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    expect(t.calls).toHaveLength(1); // still only the first — the second waits

    await t.settle();
    expect(t.calls.map((c) => c.value)).toEqual([{ distance: 1 }, { distance: 2 }]);
  });

  it("keeps only the newest write waiting", async () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);

    for (const distance of [2, 3, 4]) {
      w.save({ distance });
      vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    }

    await t.settle();
    expect(t.calls.map((c) => c.value)).toEqual([{ distance: 1 }, { distance: 4 }]);
  });

  it("sends a reset without waiting for the debounce", () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    w.save({ distance: 1 });
    w.reset();
    expect(t.calls).toEqual([{ kind: "reset" }]);
  });

  it("drops a save that was waiting behind a reset", async () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    // A save goes on the wire...
    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    // ...a second is queued behind it...
    w.save({ distance: 2 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    // ...and Reset arrives before either finishes.
    w.reset();

    await t.settle(); // the first save completes
    await t.settle(); // then whatever the queue chose to run

    expect(t.calls).toEqual([
      { kind: "save", value: { distance: 1 } },
      { kind: "reset" },
    ]);
  });

  it("does not let a pre-reset save restore what the reset cleared", async () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS); // in flight
    w.save({ distance: 2 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS); // queued
    w.reset();

    await t.settle();
    await t.settle();
    await t.settle();

    const kinds = t.calls.map((c) => c.kind);
    expect(kinds[kinds.length - 1]).toBe("reset");
    expect(t.calls.filter((c) => c.value?.distance === 2)).toHaveLength(0);
  });

  it("flush sends a pending edit immediately", () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    w.save({ distance: 7 });
    w.flush();
    expect(t.calls).toEqual([{ kind: "save", value: { distance: 7 } }]);
  });

  it("flush is a no-op with nothing pending", () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);
    w.flush();
    expect(t.calls).toHaveLength(0);
  });

  it("reports a failed write instead of throwing", async () => {
    const onError = vi.fn();
    const transport: PreferenceTransport = {
      get: async () => ({}),
      save: async () => {
        throw new Error("503");
      },
      reset: async () => {},
    };
    const w = new PreferenceWriter(transport, { onError });

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await w.settled();

    expect(onError).toHaveBeenCalledOnce();
  });

  it("keeps draining the queue after a failure", async () => {
    const seen: FilterState[] = [];
    let fail = true;
    const transport: PreferenceTransport = {
      get: async () => ({}),
      save: async (filters) => {
        seen.push(filters);
        if (fail) {
          fail = false;
          throw new Error("503");
        }
      },
      reset: async () => {},
    };
    const w = new PreferenceWriter(transport, { onError: () => {} });

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    w.save({ distance: 2 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await w.settled();

    expect(seen).toEqual([{ distance: 1 }, { distance: 2 }]);
  });
});

describe("localStoragePreferences", () => {
  const key = "patient-1";

  // A minimal in-memory Storage rather than jsdom: the transport only needs
  // getItem/setItem/removeItem, and this suite runs in the `node` project,
  // which the config keeps deliberately cheap.
  class MemoryStorage {
    private data = new Map<string, string>();
    failOnWrite = false;
    getItem(k: string) {
      return this.data.has(k) ? this.data.get(k)! : null;
    }
    setItem(k: string, v: string) {
      if (this.failOnWrite) throw new Error("QuotaExceededError");
      this.data.set(k, v);
    }
    removeItem(k: string) {
      this.data.delete(k);
    }
  }

  let store: MemoryStorage;

  beforeEach(() => {
    store = new MemoryStorage();
    (globalThis as { localStorage?: unknown }).localStorage = store;
  });

  afterEach(() => {
    delete (globalThis as { localStorage?: unknown }).localStorage;
  });

  it("round-trips filters", async () => {
    const prefs = localStoragePreferences(key);
    await prefs.save({ country: "US", distance: 25 });
    expect(await prefs.get()).toEqual({ country: "US", distance: 25 });
  });

  it("scopes by patient, so two patients do not share filters", async () => {
    await localStoragePreferences("patient-1").save({ distance: 1 });
    expect(await localStoragePreferences("patient-2").get()).toEqual({});
  });

  it("reads nothing as no filters", async () => {
    expect(await localStoragePreferences(key).get()).toEqual({});
  });

  it("treats a corrupt entry as no filters rather than throwing", async () => {
    store.setItem(`exact.filters.${key}`, "{not json");
    expect(await localStoragePreferences(key).get()).toEqual({});
  });

  it("treats a non-object entry as no filters", async () => {
    store.setItem(`exact.filters.${key}`, '"a string"');
    expect(await localStoragePreferences(key).get()).toEqual({});
  });

  it("reset clears them", async () => {
    const prefs = localStoragePreferences(key);
    await prefs.save({ distance: 5 });
    await prefs.reset();
    expect(await prefs.get()).toEqual({});
  });

  it("survives storage that refuses to write", async () => {
    store.failOnWrite = true;
    // A filter that cannot persist must not break the search on screen.
    await expect(
      localStoragePreferences(key).save({ distance: 1 }),
    ).resolves.toBeUndefined();
  });

  it("degrades to no filters where there is no storage at all", async () => {
    delete (globalThis as { localStorage?: unknown }).localStorage;
    const prefs = localStoragePreferences(key);
    expect(await prefs.get()).toEqual({});
    await expect(prefs.save({ distance: 1 })).resolves.toBeUndefined();
  });
});

describe("PreferenceWriter — a reset is a barrier", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("still resets when an edit follows it during the same flight", async () => {
    // Reset and a later save are not alternatives: both have to happen, in
    // that order. Collapsed into one slot, the save overwrote the reset and
    // the partial update already on the server was never cleared.
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS); // in flight
    w.reset(); // queued behind it
    w.save({ distance: 9 }); // must NOT take the reset's place
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);

    await t.settle(); // first save completes → reset runs
    await t.settle(); // reset completes → the post-reset save runs
    await t.settle();

    expect(t.calls.map((c) => c.kind)).toEqual(["save", "reset", "save"]);
    expect(t.calls.at(-1)?.value).toEqual({ distance: 9 });
  });
});

describe("adapterPreferences", () => {
  function fakeAdapter() {
    const saved: Array<Record<string, unknown>> = [];
    let stored: Record<string, unknown> = {};
    return {
      saved,
      setStored: (v: Record<string, unknown>) => {
        stored = v;
      },
      methods: {
        getPreferences: async () => stored as never,
        savePreferences: async (f: never) => {
          saved.push(f as Record<string, unknown>);
        },
        resetPreferences: async () => {
          stored = {};
        },
      },
    };
  }

  it("drops a cleared key from the payload, because the endpoint replaces", async () => {
    // The stored dict is assigned wholesale, so a key disappears by being
    // absent. `FilterPanel` represents a cleared control as `undefined` and
    // JSON drops it, which is exactly the behaviour wanted — what must NOT
    // happen is the key surviving in the payload as a null.
    const a = fakeAdapter();
    const t = adapterPreferences(a.methods);

    await t.save({ searchTitle: "vrd", distance: 50 });
    await t.save({ distance: 50, searchTitle: undefined });

    expect(a.saved[0]).toEqual({ searchTitle: "vrd", distance: 50 });
    expect(a.saved[1]).toEqual({ distance: 50 });
  });

  it("does not read a cleared key back as a set one", async () => {
    const a = fakeAdapter();
    a.setStored({ searchTitle: null, distance: 50 });
    expect(await adapterPreferences(a.methods).get()).toEqual({ distance: 50 });
  });

  it("forgets what it sent once the preferences are reset", async () => {
    const a = fakeAdapter();
    const t = adapterPreferences(a.methods);
    await t.save({ searchTitle: "vrd" });
    await t.reset();
    await t.save({ distance: 10 });
    // No stale `searchTitle: null` — the reset already cleared the row.
    expect(a.saved.at(-1)).toEqual({ distance: 10 });
  });
});

describe("adapterPreferences — clearing what the server already held", () => {
  it("clears a key that came from the server, not just one it sent", async () => {
    // The key has to reach the payload before it can be left out of it: the
    // transport merges over what it read, so a cleared value must arrive as
    // present-and-`undefined` to cancel what the read put there.
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => ({ searchTitle: "vrd" }) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    expect(await t.get()).toEqual({ searchTitle: "vrd" });
    // `userOwnedFilters` emits a cleared owned field present-and-undefined.
    await t.save({ searchTitle: undefined });

    expect(saved[0]).toEqual({});
  });
});

describe("adapterPreferences — a save that overtakes the first read", () => {
  it("waits for the read, so the seed is in place before the payload is built", async () => {
    // A slow preference load plus an edit inside the debounce window. Without
    // serialising, the payload is built against an empty memory — and since
    // the endpoint replaces the stored dict, that payload deletes everything
    // the reader had saved.
    const saved: Array<Record<string, unknown>> = [];
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const t = adapterPreferences({
      getPreferences: async () => {
        await gate;
        return { searchTitle: "vrd" } as never;
      },
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    const read = t.get();
    const write = t.save({ distance: 50 });
    expect(saved).toEqual([]); // still behind the read

    release();
    await read;
    await write;

    expect(saved[0]).toEqual({ searchTitle: "vrd", distance: 50 });
  });
});

describe("adapterPreferences — what the reader is not holding", () => {
  it("carries the keys it read but was never given back", async () => {
    // A load that was read and deliberately discarded leaves the caller with a
    // strict subset of what is stored, and the endpoint REPLACES the stored
    // dict rather than merging into it — so a save carrying only the reader's
    // one edit deletes the rest: filters they never saw, on a slow connection
    // only. The transport merges its payload over what it read instead.
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => ({ sponsor: "Janssen", phase: "2" }) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    await t.get();
    await t.save({ searchTitle: "dara" });

    expect(saved[0]).toEqual({ sponsor: "Janssen", phase: "2", searchTitle: "dara" });
  });

  it("forgets what the row held even when the reset fails", async () => {
    // The opposite of what a merging endpoint would want. Under a replace, a
    // memory still full of the old values means the next save puts them all
    // back: the reader watches their filters disappear and finds them again on
    // the next mount. Clearing first makes the next save repair the reset.
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => ({ sponsor: "Janssen" }) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {
        throw new Error("503");
      },
    });

    await t.get();
    await expect(t.reset()).rejects.toThrow("503");
    await t.save({ searchTitle: "dara" });

    expect(saved[0]).toEqual({ searchTitle: "dara" });
  });
});

describe("localStoragePreferences", () => {
  // This file runs in the node project, which has no DOM. A map is all the
  // transport asks of `localStorage`.
  let store: Record<string, string>;
  beforeEach(() => {
    store = {};
    (globalThis as Record<string, unknown>).localStorage = {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => {
        store[k] = v;
      },
      removeItem: (k: string) => {
        delete store[k];
      },
    };
  });
  afterEach(() => {
    delete (globalThis as Record<string, unknown>).localStorage;
  });

  it("does not put the patient key on disk in the clear", async () => {
    // `key` is the patient key, and for an inline payload that IS the patient
    // record. It was a react-query cache key in memory; a storage key persists
    // it per browser, unexpired, for every patient ever viewed.
    const patient = JSON.stringify({ disease: "multiple myeloma", dob: "1961-04-02" });
    await localStoragePreferences(`|${patient}`).save({ searchTitle: "vrd" });
    const keys = Object.keys(store);
    expect(keys).toHaveLength(1);
    expect(keys[0]).not.toContain("myeloma");
    expect(keys[0]).not.toContain("1961");
  });

  it("merges rather than replacing, and reads a cleared key as unset", async () => {
    const t = localStoragePreferences("p1");
    await t.save({ sponsor: "Janssen", searchTitle: "vrd" });
    await t.save({ searchTitle: "dara" });
    // `sponsor` was never mentioned by the second write, so it survives...
    expect(await t.get()).toEqual({ sponsor: "Janssen", searchTitle: "dara" });

    await t.save({ searchTitle: undefined });
    // ...while a key that IS mentioned, as cleared, reads back as unset.
    expect(await t.get()).toEqual({ sponsor: "Janssen" });
  });
});

describe("PreferenceWriter — a transport that throws synchronously", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reports it instead of leaving an unhandled rejection", async () => {
    // A host adapter can throw rather than reject — a misconfigured client,
    // adapter-side validation. Outside the promise chain that bypassed
    // `onError` entirely and never assigned `inFlight`, while the class
    // promises a failed write is reported, not thrown.
    const onError = vi.fn();
    const onSuccess = vi.fn();
    const w = new PreferenceWriter(
      {
        get: async () => ({}),
        save: () => {
          throw new Error("no client");
        },
        reset: async () => {},
      },
      { onError, onSuccess },
    );

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await w.settled();

    expect(onError).toHaveBeenCalledOnce();
    // And NOT as a success as well: a resolved substitute for the throw would
    // travel the success path and announce the write that just failed.
    expect(onSuccess).not.toHaveBeenCalled();
  });
});

describe("adapterPreferences — a save that fails", () => {
  it("does not record the payload as if it had landed", async () => {
    // The failed PATCH was the one meant to clear `sponsor`. Believing it
    // succeeded, the next payload is built against a row that does not exist
    // — and the row still holds the sponsor.
    const saved: Array<Record<string, unknown>> = [];
    let fail = true;
    const t = adapterPreferences({
      getPreferences: async () => ({ sponsor: "Janssen" }) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
        if (fail) {
          fail = false;
          throw new Error("503");
        }
      },
      resetPreferences: async () => {},
    });

    await t.get();
    await expect(t.save({ searchTitle: "dara" })).rejects.toThrow("503");
    // A DIFFERENT second edit, or the two payloads come out identical whether
    // the failed one was recorded or not and the test proves nothing.
    await t.save({ phase: "2" });

    // Built against what the server actually holds — the failed write did not
    // become part of the picture.
    expect(saved[1]).toEqual({ sponsor: "Janssen", phase: "2" });
  });
});

describe("adapterPreferences — when it cannot read what it would be replacing", () => {
  it("refuses to write rather than replace the row blind", async () => {
    // An empty memory because the row is empty and an empty memory because the
    // READ failed are the same value and opposite situations. Under a replace,
    // writing in the second case costs the reader every filter they ever
    // saved; refusing costs them one edit they can make again.
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => {
        throw new Error("503");
      },
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    await expect(t.get()).rejects.toThrow("503");
    await expect(t.save({ distance: 50 })).rejects.toThrow(/could not be read/);
    expect(saved).toEqual([]);
  });

  it("writes once a retry tells it what is there", async () => {
    // A read that failed once may not fail twice, and the reader's edit is
    // worth one more attempt before it is dropped.
    const saved: Array<Record<string, unknown>> = [];
    let attempts = 0;
    const t = adapterPreferences({
      getPreferences: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("503");
        return { sponsor: "Janssen" } as never;
      },
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    await expect(t.get()).rejects.toThrow("503");
    await t.save({ distance: 50 });

    expect(saved[0]).toEqual({ sponsor: "Janssen", distance: 50 });
  });

  it("treats a row that is genuinely empty as something it can write to", async () => {
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => ({}) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    await t.get();
    await t.save({ distance: 50 });
    expect(saved[0]).toEqual({ distance: 50 });
  });
});

describe("adapterPreferences — a read that lands after a Reset", () => {
  it("does not put back what the reader just cleared", async () => {
    // The read was in flight when they pressed Reset, so it resolves holding
    // the values they had just watched disappear. Seeding from it puts them in
    // the next payload, and under a replace that payload is the row.
    const saved: Array<Record<string, unknown>> = [];
    let release!: (v: Record<string, unknown>) => void;
    const gate = new Promise<Record<string, unknown>>((r) => {
      release = r;
    });
    const t = adapterPreferences({
      getPreferences: () => gate as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    const read = t.get();
    await t.reset();
    release({ sponsor: "Janssen", phase: "2" });
    await read;

    await t.save({ searchTitle: "dara" });
    expect(saved[0]).toEqual({ searchTitle: "dara" });
  });
});

describe("PreferenceWriter — recovering from a failed write", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reports the write that lands, not only the one that failed", async () => {
    // Writes queue, so a caller showing "couldn't save" has no way to learn
    // that the NEXT one succeeded unless it is told. Clearing the message when
    // a write is ISSUED does not work either: an edit made while a failing
    // write is in flight clears it before that failure arrives.
    const seen: string[] = [];
    let fail = true;
    const w = new PreferenceWriter(
      {
        get: async () => ({}),
        save: async () => {
          if (fail) {
            fail = false;
            throw new Error("503");
          }
        },
        reset: async () => {},
      },
      { onError: () => seen.push("error"), onSuccess: () => seen.push("success") },
    );

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await w.settled();
    expect(seen).toEqual(["error"]);

    w.save({ distance: 2 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await w.settled();
    expect(seen).toEqual(["error", "success"]);
  });
});

describe("adapterPreferences — a reset that fails before anything was read", () => {
  it("still refuses the next save rather than replacing the row blind", async () => {
    // Clearing the memory on a failed reset is right; claiming to KNOW the row
    // is empty is not. With no successful read behind it, the next save would
    // replace whatever is there with the one filter the reader is holding.
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => {
        throw new Error("503");
      },
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {
        throw new Error("503");
      },
    });

    await expect(t.get()).rejects.toThrow("503");
    await expect(t.reset()).rejects.toThrow("503");
    await expect(t.save({ distance: 50 })).rejects.toThrow(/could not be read/);
    expect(saved).toEqual([]);
  });

  it("repairs a failed reset when the row WAS known", async () => {
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => ({ sponsor: "Janssen" }) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {
        throw new Error("503");
      },
    });

    await t.get();
    await expect(t.reset()).rejects.toThrow("503");
    await t.save({ distance: 50 });

    // Only what the reader is holding: the reset the server refused is carried
    // out by the next write.
    expect(saved[0]).toEqual({ distance: 50 });
  });
});

describe("adapterPreferences, conditionally", () => {
  // The lost update this exists to stop: `stored` is a belief only THIS
  // instance updates, so another tab writing the row leaves it stale and
  // confident, and the merge then faithfully reconstructs a set that is no
  // longer true. promop#1312 gave the server a way to say no; these check
  // that we ask, and that we do the right thing when it does.

  /** A versioning transport whose row the test can move under the caller. */
  function versionedAdapter(initial: Record<string, unknown> = {}, version: string | null = null) {
    const writes: Array<{ filters: Record<string, unknown>; precondition: Precondition }> = [];
    const clears: Precondition[] = [];
    let row = { ...initial };
    let tag = version;
    let reads = 0;
    let plainReads = 0;
    const api = {
      writes,
      clears,
      readCount: () => reads,
      /** Reads that went through the UNVERSIONED method. The blind-retry
       *  path used to use this one, which is invisible to `readCount` — so
       *  a test counting only versioned reads could not tell the fixed code
       *  from the broken code. */
      plainReadCount: () => plainReads,
      /** Simulate another tab landing a write. */
      elsewhere: (next: Record<string, unknown>, nextTag: string) => {
        row = { ...next };
        tag = nextTag;
      },
      current: () => ({ row: { ...row }, tag }),
      methods: {
        getPreferences: async () => {
          plainReads += 1;
          return row as never;
        },
        savePreferences: async () => undefined,
        resetPreferences: async () => undefined,
        preferenceVersioning: {
          read: async (): Promise<VersionedPreferences> => {
            reads += 1;
            return { filters: { ...row } as never, version: tag };
          },
          write: async (
            filters: FilterState,
            precondition: Precondition,
          ): Promise<string | null> => {
            writes.push({ filters: { ...(filters as object) }, precondition });
            // The fake used to accept an `ifMatch` carrying `undefined` and
            // model it as a mismatch. The real adapter puts that value in a
            // header, and axios DELETES a header whose value is undefined —
            // so production wrote unconditionally where the fake 412'd, and
            // the one test written for that case exercised a path production
            // never reached. Refuse to model what cannot happen.
            if (precondition.kind === "ifMatch" && typeof precondition.version !== "string") {
              throw new Error("ifMatch without a string version reaches the wire as no precondition");
            }
            if (precondition.kind === "ifMatch" && precondition.version !== tag) {
              throw new PreconditionFailed(tag);
            }
            if (precondition.kind === "ifNoneMatch" && tag !== null) {
              throw new PreconditionFailed(tag);
            }
            row = { ...(filters as object) };
            tag = `"v${writes.length}"`;
            return tag;
          },
          clear: async (precondition: Precondition): Promise<string | null> => {
            clears.push(precondition);
            if (precondition.kind === "ifMatch" && precondition.version !== tag) {
              throw new PreconditionFailed(tag);
            }
            row = {};
            tag = `"cleared${clears.length}"`;
            return tag;
          },
        },
      },
    };
    return api;
  }

  it("asks If-None-Match for the first write, which If-Match cannot describe", async () => {
    // Before the row exists there is no tag to quote, so two tabs
    // bootstrapping the same patient would otherwise both write blind.
    const a = versionedAdapter({}, null);
    const t = adapterPreferences(a.methods);

    await t.get();
    await t.save({ distance: 50 });

    expect(a.writes[0].precondition).toEqual({ kind: "ifNoneMatch" });
  });

  it("quotes the version it read on every later write", async () => {
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);

    await t.get();
    await t.save({ distance: 50 });

    expect(a.writes[0].precondition).toEqual({ kind: "ifMatch", version: '"v0"' });
  });

  it("carries the new version forward without re-reading", async () => {
    // Else every save would need a read in front of it, which is a request
    // per keystroke once the debounce lets go.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);

    await t.get();
    const readsAfterGet = a.readCount();
    await t.save({ distance: 50 });
    await t.save({ distance: 75 });

    expect(a.readCount()).toBe(readsAfterGet);
    expect(a.writes[1].precondition).toEqual({ kind: "ifMatch", version: '"v1"' });
  });

  it("re-applies the reader's edit over the OTHER tab's row, not over its own stale belief", async () => {
    // The assertion that matters. Tab A read {sponsor}. Tab B cleared it and
    // added {phase}. Tab A now ticks distance. Resending A's merged payload
    // would restore `sponsor` and delete `phase` — the lost update with an
    // extra round trip. What must land is B's row plus A's one edit.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({ phase: "PHASE3" }, '"v-other"');
    await t.save({ distance: 50 });

    expect(a.writes[0].filters).toEqual({ sponsor: "Acme", distance: 50 });
    expect(a.writes[0].precondition).toEqual({ kind: "ifMatch", version: '"v0"' });
    // The retry, after the refusal.
    expect(a.writes[1].filters).toEqual({ phase: "PHASE3", distance: 50 });
    expect(a.writes[1].precondition).toEqual({ kind: "ifMatch", version: '"v-other"' });
    expect(a.current().row).toEqual({ phase: "PHASE3", distance: 50 });
  });

  it("gives up after one retry rather than looping", async () => {
    // Twice in a row is live contention, not a stale cache. A third attempt
    // would be a loop; `PreferenceWriter` reports this through `onError`.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    // One writer lands before our save — that is the first 412 — and another
    // lands while we are re-reading, so the retry is stale on arrival too.
    a.elsewhere({ phase: "PHASE3" }, '"v-other"');
    const versioning = a.methods.preferenceVersioning;
    const realRead = versioning.read;
    versioning.read = async () => {
      const out = await realRead();
      a.elsewhere({ sponsor: "Third" }, '"v-moved-again"');
      return out;
    };

    await expect(t.save({ distance: 50 })).rejects.toThrow(/changed elsewhere/);
    expect(a.writes).toHaveLength(2);
  });

  it("does not carry a tag newer than what it believes the row holds", async () => {
    // The lost update, one step further along. After a second refusal the
    // server's tag describes a row we never read, while `stored` still holds
    // the FIRST re-read. Adopting that tag would make the next save's
    // precondition pass and overwrite the third writer's content.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({ phase: "PHASE3" }, '"v-other"');
    const versioning = a.methods.preferenceVersioning;
    const realRead = versioning.read;
    versioning.read = async () => {
      const out = await realRead();
      a.elsewhere({ country: "US" }, '"v-third"');
      return out;
    };
    await expect(t.save({ distance: 50 })).rejects.toThrow(/changed elsewhere/);
    versioning.read = realRead;

    // The reader edits again. This save must go and look, not assume.
    await t.save({ distance: 75 });

    expect(a.current().row).toEqual({ country: "US", distance: 75 });
  });

  it("reads before writing when the version is unknown", async () => {
    // A write that cannot report its new tag leaves the version unknown,
    // which is NOT the same as "there is no row". Guessing `If-None-Match`
    // there would 412 against a row that plainly exists.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    const versioning = a.methods.preferenceVersioning;
    const realWrite = versioning.write;
    versioning.write = async (filters, precondition) => {
      await realWrite(filters, precondition);
      return null; // the transport cannot say what the new version is
    };
    await t.save({ distance: 50 });
    versioning.write = realWrite;

    const before = a.readCount();
    const writesBefore = a.writes.length;
    await t.save({ distance: 75 });

    expect(a.readCount()).toBe(before + 1);
    // Exactly ONE write, with a real tag. Asserting only the final
    // precondition would let this pass the wrong way: with `null` treated as
    // "no row" the save sends `If-None-Match: *`, is refused, re-reads and
    // retries — reaching the same end state through a 412 nobody needed.
    expect(a.writes.length).toBe(writesBefore + 1);
    expect(a.writes[a.writes.length - 1].precondition).toMatchObject({ kind: "ifMatch" });
  });

  it("treats a reset that cannot report its tag as unknown, not as no-row", async () => {
    // PROMOP's reset EMPTIES the row, it does not delete it. So a `clear`
    // answering `null` — the contract's "cannot say", which the seam's own
    // fallback returns by construction — must not leave the transport
    // believing there is no row: the next save would aim
    // `If-None-Match: *` at a row the reset just left standing.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    const versioning = a.methods.preferenceVersioning;
    const realClear = versioning.clear;
    versioning.clear = async (precondition) => {
      await realClear(precondition);
      return null;
    };
    await t.reset();
    versioning.clear = realClear;

    const writesBefore = a.writes.length;
    await t.save({ distance: 50 });

    expect(a.writes.length).toBe(writesBefore + 1);
    expect(a.writes[a.writes.length - 1].precondition).toMatchObject({ kind: "ifMatch" });
  });

  it("falls back to the refusal's tag when the re-read cannot describe the row", async () => {
    // The line that stops a silent overwrite, on the path that reaches it:
    // a 412 that DID carry a tag, followed by a re-read that cannot produce
    // one. Without the fallback the retry goes out unconditional and
    // replaces whatever a third writer put there, with no 412 and nothing
    // on `onError`.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({ phase: "PHASE3" }, '"v-other"');
    const versioning = a.methods.preferenceVersioning;
    const realRead = versioning.read;
    versioning.read = async () => ({
      ...(await realRead()),
      version: undefined, // the list row carries no updated_at
    });

    await t.save({ distance: 50 });

    // The retry quotes the tag the refusal reported, not nothing.
    expect(a.writes[1].precondition).toEqual({ kind: "ifMatch", version: '"v-other"' });
  });

  it("strips nulls from the row it re-reads when the version was unknown", async () => {
    // Sibling of the 412 re-read, and the one that was left uncovered: the
    // unknown-version refresh. A null here is re-persisted on every later
    // save.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    // Leave the version unknown so the next save takes the refresh path.
    const versioning = a.methods.preferenceVersioning;
    const realWrite = versioning.write;
    versioning.write = async (filters, precondition) => {
      await realWrite(filters, precondition);
      return null;
    };
    await t.save({ distance: 50 });
    versioning.write = realWrite;

    a.elsewhere({ sponsor: null, country: "US" }, '"v-other"');
    await t.save({ phase: "PHASE3" });

    expect(a.writes[a.writes.length - 1].filters).toEqual({
      country: "US",
      phase: "PHASE3",
    });
  });

  it("recovers when the row cannot describe itself", async () => {
    // A list response missing `updated_at` used to read as "no row", so
    // every save sent `If-None-Match: *`, was refused, re-read the same
    // answer, was refused again — and saved filters stopped working for that
    // patient permanently. The refusal carries the tag that breaks the loop.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    const versioning = a.methods.preferenceVersioning;
    versioning.read = async () => ({ filters: { sponsor: "Acme" } as never, version: undefined });

    await t.get();
    await t.save({ distance: 50 });

    expect(a.current().row).toEqual({ sponsor: "Acme", distance: 50 });
  });

  it("writes with NO precondition when the row cannot be described, not a broken one", async () => {
    // The failure this replaced was silent: `version as string` let
    // `undefined` reach `If-Match`, axios deletes a header with an undefined
    // value, and the request went out unconditional while the caller
    // believed it was protected. Degrading is defensible; degrading
    // invisibly is not, so the shape is asserted rather than the outcome.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    const versioning = a.methods.preferenceVersioning;
    versioning.read = async () => ({
      filters: { sponsor: "Acme" } as never,
      version: undefined,
    });

    await t.get();
    await t.save({ distance: 50 });

    expect(a.writes[0].precondition).toEqual({ kind: "none" });
  });

  it("strips nulls from the row it re-reads after a refusal", async () => {
    // The read paths strip them; the two paths added later did not, so a
    // 412 retry re-persisted a null on every later save.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({ sponsor: null, country: "US" }, '"v-other"');
    await t.save({ distance: 50 });

    expect(a.writes[1].filters).toEqual({ country: "US", distance: 50 });
  });

  it("drops a write that a Reset overtook on the wire", async () => {
    // The successful-write assignment pair, which recorded its payload
    // whatever had happened while it was in flight.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    const versioning = a.methods.preferenceVersioning;
    const realWrite = versioning.write;
    versioning.write = async (filters, precondition) => {
      const tag = await realWrite(filters, precondition);
      await t.reset();
      return tag;
    };
    await t.save({ distance: 50 });
    versioning.write = realWrite;

    // Reset is the newer intent, so the next save must not carry the
    // pre-reset filters forward. Asserting the final row alone would not
    // discriminate: without the guard the stale state is written, refused,
    // re-read and retried, arriving at the same place through two extra
    // requests. The request COUNT is the mechanism.
    const writesBefore = a.writes.length;
    await t.save({ phase: "PHASE3" });

    expect(a.writes.length).toBe(writesBefore + 1);
    expect(a.current().row).toEqual({ phase: "PHASE3" });
  });

  it("drops a RETRY that a Reset overtook on the wire", async () => {
    // Same guard, on the second attempt. The retry is the path that already
    // knows the row moved once, so it is the likeliest to be in flight when
    // the reader gives up and presses Reset.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();
    a.elsewhere({ phase: "PHASE3" }, '"v-other"');

    const versioning = a.methods.preferenceVersioning;
    const realWrite = versioning.write;
    let attempts = 0;
    versioning.write = async (filters, precondition) => {
      attempts += 1;
      if (attempts === 2) {
        const tag = await realWrite(filters, precondition);
        await t.reset();
        return tag;
      }
      return realWrite(filters, precondition);
    };
    await t.save({ distance: 50 });
    versioning.write = realWrite;

    const writesBefore = a.writes.length;
    await t.save({ country: "US" });

    expect(a.writes.length).toBe(writesBefore + 1);
    expect(a.current().row).toEqual({ country: "US" });
  });

  it("treats a refused reset's untaggable clear as unknown too", async () => {
    // The recovery path clears a second time; its `null` needs the same
    // normalising as the first, or the next save aims `If-None-Match: *` at
    // the row the reset just emptied but did not delete.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();
    a.elsewhere({ phase: "PHASE3" }, '"v-other"');

    const versioning = a.methods.preferenceVersioning;
    const realClear = versioning.clear;
    let calls = 0;
    versioning.clear = async (precondition) => {
      calls += 1;
      const tag = await realClear(precondition);
      return calls === 2 ? null : tag;
    };
    await t.reset();
    versioning.clear = realClear;

    const writesBefore = a.writes.length;
    await t.save({ distance: 50 });

    expect(a.writes.length).toBe(writesBefore + 1);
    expect(a.writes[a.writes.length - 1].precondition).toMatchObject({ kind: "ifMatch" });
  });

  it("abandons a pending edit when a Reset overtakes the refresh", async () => {
    // Same rule as the refusal path: Reset is the newer intent. Declining to
    // adopt the refresh is not enough — the save would go on to write the
    // pending edit against the version Reset installed, putting back exactly
    // what Reset removed.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    // Leave the version unknown, which is what sends the next save through
    // the refresh path.
    const versioning = a.methods.preferenceVersioning;
    const realWrite = versioning.write;
    versioning.write = async (filters, precondition) => {
      await realWrite(filters, precondition);
      return null;
    };
    await t.save({ distance: 50 });
    versioning.write = realWrite;

    const realRead = versioning.read;
    versioning.read = async () => {
      // Reset FIRST, so the read observes the post-reset row and returns its
      // version. Snapshotting before the reset let a mutant that deletes the
      // guard outright survive: it adopted a pre-reset version and was
      // caught by a 412 it had not earned. Observing the emptied row is just
      // as realistic — the request is issued before the Reset and served
      // after it — and it leaves the guard as the only thing standing
      // between the pending edit and the row Reset emptied.
      await t.reset();
      return realRead();
    };

    await t.save({ distance: 75 });

    expect(a.current().row).toEqual({});
  });

  it("seeds the version from the same read that seeds the filters", async () => {
    // The blind-retry path used the unversioned read, so `stored` was filled
    // in while the version stayed unknown.
    //
    // Asserting only the final precondition would be tautological: the
    // unknown-version refresh added alongside this repairs the state before
    // the write, so the broken version reaches the same precondition — just
    // after a second read nobody needed. The read COUNT is what tells the
    // two apart.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);

    // No `get()` first: this is the path `save` takes when it has never read.
    await t.save({ distance: 50 });

    // The count that tells the two apart is the UNVERSIONED one: the broken
    // version read through `getPreferences`, which `readCount` never saw,
    // and the unknown-version refresh then repaired the state before the
    // write — so both the read count and the final precondition matched.
    expect(a.plainReadCount()).toBe(0);
    expect(a.readCount()).toBe(1);
    expect(a.writes[0].precondition).toEqual({ kind: "ifMatch", version: '"v0"' });
  });

  it("does not resurrect an edit that a Reset overtook", async () => {
    // Reset is the newer intent. Putting the edit back on top of what Reset
    // removed is what the generation counter exists to prevent.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();
    a.elsewhere({ phase: "PHASE3" }, '"v-other"');

    const versioning = a.methods.preferenceVersioning;
    const realRead = versioning.read;
    versioning.read = async () => {
      const out = await realRead();
      await t.reset();
      return out;
    };

    await t.save({ distance: 50 });

    expect(a.current().row).toEqual({});
  });

  it("clears conditionally, and clears anyway when the row moved", async () => {
    // A reset does not need to preserve what it is about to delete, so a
    // refusal is answered by clearing unconditionally rather than by
    // dropping the reader's explicit intent.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({ phase: "PHASE3" }, '"v-other"');
    await t.reset();

    expect(a.clears[0]).toEqual({ kind: "ifMatch", version: '"v0"' });
    expect(a.clears[1]).toEqual({ kind: "none" });
    expect(a.current().row).toEqual({});
  });

  it("stays unconditional for an adapter that does not offer versioning", async () => {
    // ht-phr's and CB's adapters predate promop#1312, and `localStorage` has
    // no second writer. Both must keep working exactly as before.
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => ({ sponsor: "Acme" }) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => undefined,
    });

    await t.get();
    await t.save({ distance: 50 });

    expect(saved[0]).toEqual({ sponsor: "Acme", distance: 50 });
  });
});
