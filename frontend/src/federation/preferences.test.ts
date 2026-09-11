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
    forget: () => {},
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
      forget: () => {},
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
      forget: () => {},
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

  it("sends a cleared key explicitly, because the endpoint merges", async () => {
    // `FilterPanel` represents a cleared control as `undefined`, which JSON
    // drops — so under merge semantics the old value would survive and come
    // back on the next mount.
    const a = fakeAdapter();
    const t = adapterPreferences(a.methods);

    await t.save({ searchTitle: "vrd", distance: 50 });
    await t.save({ distance: 50 });

    expect(a.saved[0]).toEqual({ searchTitle: "vrd", distance: 50 });
    expect(a.saved[1]).toEqual({ searchTitle: null, distance: 50 });
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
    // `lastSent` starts empty, so without seeding it from `get()` the payload
    // would simply omit the key and the merge would keep the old value.
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => ({ searchTitle: "vrd" }) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    expect(await t.get()).toEqual({ searchTitle: "vrd" });
    await t.save({}); // the reader cleared it, having changed nothing else

    expect(saved[0]).toEqual({ searchTitle: null });
  });
});

describe("adapterPreferences — a save that overtakes the first read", () => {
  it("waits for the read, so the seed is in place before the payload is built", async () => {
    // A slow preference load plus an edit inside the debounce window. Without
    // serialising, the PATCH is built against an empty `lastSent` and the keys
    // the server already held survive the merge.
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

    expect(saved[0]).toEqual({ searchTitle: null, distance: 50 });
  });
});

describe("adapterPreferences — what it does NOT know about", () => {
  it("stops nulling keys once it is told to forget them", async () => {
    // A load that was read and deliberately discarded leaves the caller with a
    // strict subset of what is stored. Without `forget` the next save reads as
    // "these are all the filters there are" and clears the rest — filters the
    // reader never saw, on a slow connection only.
    const saved: Array<Record<string, unknown>> = [];
    const t = adapterPreferences({
      getPreferences: async () => ({ sponsor: "Janssen", phase: "2" }) as never,
      savePreferences: async (f: never) => {
        saved.push(f as Record<string, unknown>);
      },
      resetPreferences: async () => {},
    });

    await t.get();
    t.forget();
    await t.save({ searchTitle: "dara" });

    expect(saved[0]).toEqual({ searchTitle: "dara" });
  });

  it("keeps its seed when a reset fails", async () => {
    // Clearing the seed before the request resolves means a failed reset
    // leaves us believing the server is empty. The next save then nulls
    // nothing, and what the reader watched disappear is back on the next
    // mount.
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

    expect(saved[0]).toEqual({ sponsor: null, searchTitle: "dara" });
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
    t.forget();
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
    const w = new PreferenceWriter(
      {
        get: async () => ({}),
        save: () => {
          throw new Error("no client");
        },
        reset: async () => {},
        forget: () => {},
      },
      { onError },
    );

    w.save({ distance: 1 });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await w.settled();

    expect(onError).toHaveBeenCalledOnce();
  });
});

describe("adapterPreferences — a save that fails", () => {
  it("does not record the payload as if it had landed", async () => {
    // The failed PATCH was the one meant to clear `sponsor`. Believing it
    // succeeded, the next payload stops nulling it and it comes back.
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
    await t.save({ searchTitle: "dara" });

    expect(saved[1]).toEqual({ sponsor: null, searchTitle: "dara" });
  });
});
