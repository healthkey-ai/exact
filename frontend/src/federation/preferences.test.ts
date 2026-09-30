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
    forget: () => {},
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
      forget: () => {},
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
      forget: () => {},
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
        forget: () => {},
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
        forget: () => {},
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

  it("re-reads once told the row was written by another route", async () => {
    // The weights wizard records "this reader has been asked" as a COLUMN on
    // this row, through `upsert`, not through this transport. That moves
    // `updated_at` — which is the tag — so the cached one is stale from that
    // moment and the next save here is refused.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();
    await t.save({ distance: 50 });
    a.elsewhere({ sponsor: "Acme", distance: 50 }, '"afterTheFlag"');

    t.forget();
    const writesBefore = a.writes.length;
    await t.save({ distance: 60 });

    // ONE attempt, not a refusal and a retry — which is the whole saving, and
    // the only thing that distinguishes this from doing nothing: the recovery
    // path reaches the same tag either way, just two round trips later, and
    // in a second tab it can drop the edit instead.
    expect(a.writes.length - writesBefore).toBe(1);
    expect(a.writes.at(-1)!.precondition).toEqual({
      kind: "ifMatch",
      version: '"afterTheFlag"',
    });
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

  it("re-applies only what CHANGED, not the whole panel it was handed", async () => {
    // Regression: found by /qa on 2026-09-15 driving two real browser tabs
    // against a real promop. Report:
    // .gstack/qa-reports/qa-report-saved-filters-2026-09-15.md
    //
    // Every other test here calls `save` with just the key that moved. The
    // real caller does not: `TrialMatches.handleFiltersChange` persists
    // `userOwnedFilters` — the
    // WHOLE panel, every field the reader owns. So "re-apply the reader's
    // edit over the fresh row" re-asserted a panel that still displayed the
    // filter the other tab had just cleared, and put it back. The
    // precondition fired, the retry carried the right tag, and the update
    // was lost anyway.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    // Another tab clears `sponsor`.
    a.elsewhere({}, '"v-other"');

    // This tab's panel still SHOWS the sponsor, so it saves it alongside the
    // one field the reader actually typed into.
    await t.save({ sponsor: "Acme", searchTitle: "myeloma" });

    expect(a.writes[1].filters).toEqual({ searchTitle: "myeloma" });
    expect(a.current().row).toEqual({ searchTitle: "myeloma" });
  });

  it("keeps the other tab's clear across MORE THAN ONE save", async () => {
    // The first cut of this fix held for exactly one save. After the retry
    // the transport adopts the other tab's row, but the panel is never told —
    // nothing reconciles a fresh row back into React state — so the next
    // keystroke measured the panel's unchanged `sponsor` against a row that
    // no longer had it, scored it as an edit, and put it back with a VALID
    // `If-Match`: 200, no 412, nothing on `onError`. A 400ms debounce means
    // "type, pause, keep typing" is enough to hit it.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({}, '"v-other"');
    await t.save({ sponsor: "Acme", searchTitle: "myeloma" });
    expect(a.current().row).toEqual({ searchTitle: "myeloma" });

    // The panel still shows the sponsor it always showed.
    await t.save({ sponsor: "Acme", searchTitle: "myelomas" });

    expect(a.current().row).toEqual({ searchTitle: "myelomas" });
  });

  it("records the panel after a write that needed a retry", async () => {
    // Same rule on the recovery path. Without it the save after a refusal
    // measures against the belief from before the refusal, and re-asserts a
    // field the reader has not touched since.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({ phase: "PHASE3" }, '"v-other"');
    await t.save({ sponsor: "Acme", searchTitle: "x" });   // 412, then retry
    expect(a.current().row).toEqual({ phase: "PHASE3", searchTitle: "x" });

    // Another tab now edits the field THIS save introduced.
    a.elsewhere({ phase: "PHASE3", searchTitle: "fromB" }, '"v-b"');
    // The reader types somewhere else entirely.
    await t.save({ sponsor: "Acme", searchTitle: "x", distance: 50 });

    expect(a.current().row).toEqual({
      phase: "PHASE3",
      searchTitle: "fromB",
      distance: 50,
    });
  });

  it("still recognises a clear when the FIRST read failed", async () => {
    // `get()` throwing on mount (a network blip) leaves the panel belief
    // empty. The blind-retry read inside `save` has to seed it too, or a
    // clear reads as "nothing to clear": the payload keeps the value and the
    // reader's clear silently never lands — no 412, nothing on `onError`.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);

    const versioning = a.methods.preferenceVersioning;
    const realRead = versioning.read;
    let failed = false;
    versioning.read = async (...args) => {
      if (!failed) {
        failed = true;
        throw new Error("network blip");
      }
      return realRead(...args);
    };
    await expect(t.get()).rejects.toThrow(/network blip/);

    await t.save({ sponsor: undefined });

    expect(a.current().row).toEqual({});
  });

  it("recognises a value retyped after a Reset", async () => {
    // Reset empties the panel as well as the row. Leaving the pre-reset
    // belief in place makes the retyped value score as "no change", so it is
    // never sent: the reader watches their filter fail to save, with nothing
    // anywhere saying why.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    await t.reset();
    await t.save({ sponsor: "Acme" });

    expect(a.current().row).toEqual({ sponsor: "Acme" });
  });

  it("does not forget a field a later save left out", async () => {
    // A payload carries only the fields the panel owns, so replacing the
    // remembered panel instead of merging into it loses everything omitted
    // this time — and then a clear of one of those fields reads as "nothing
    // to clear" and silently never fires.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    await t.save({ distance: 50 });        // says nothing about `sponsor`
    await t.save({ sponsor: undefined });  // now clears it

    expect(a.current().row).toEqual({ distance: 50 });
  });

  it("records the panel after a write that needed no retry", async () => {
    // The 412 path is not the only one that has to remember what the panel
    // showed. A save that lands first time must too, or the NEXT save
    // measures against a stale belief and re-asserts a field the reader has
    // not touched — over whatever the other tab put there since.
    const a = versionedAdapter({}, null);
    const t = adapterPreferences(a.methods);
    await t.get();

    await t.save({ sponsor: "Acme" });          // lands, no refusal
    a.elsewhere({ sponsor: "FromB" }, '"v-b"'); // another tab edits it

    await t.save({ sponsor: "Acme", searchTitle: "x" });

    expect(a.current().row).toEqual({ sponsor: "FromB", searchTitle: "x" });
  });

  it("does not turn a sticky tombstone into a delete of the other tab's value", async () => {
    // `userOwnedFilters` keeps emitting a cleared owned field as
    // present-and-undefined, so the tombstone outlives the save that applied
    // it. Without the `k in before` guard the next save re-issues it as a
    // delete — of whatever the other tab has since put there.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    await t.save({ sponsor: undefined, searchTitle: "x" });
    expect(a.current().row).toEqual({ searchTitle: "x" });

    a.elsewhere({ searchTitle: "x", sponsor: "FromB" }, '"v-b"');
    // The tombstone is still in the payload; it must not fire again.
    await t.save({ sponsor: undefined, searchTitle: "xy" });

    expect(a.current().row).toEqual({ searchTitle: "xy", sponsor: "FromB" });
  });

  it("does not call an untouched multi-select an edit", async () => {
    // `trialPurpose` is an array, and the panel builds a NEW one every
    // render, so reference equality reads it as changed on every save. The
    // retry would then re-apply the reader's stale copy over the other tab's
    // change — the same lost update, through the field most likely to be
    // sitting in the panel untouched. `filters.ts` warns about exactly this
    // comparison in its own docstring.
    const a = versionedAdapter({ trialPurpose: ["treatment"] }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({ trialPurpose: ["prevention"] }, '"v-other"');
    // Same contents, different array instance — untouched by the reader.
    await t.save({ trialPurpose: ["treatment"], searchTitle: "myeloma" });

    // The payload is the whole new state — the endpoint replaces — so what
    // matters is that the other tab's selection is IN it and the reader's
    // stale copy is not.
    expect(a.writes[1].filters).toEqual({
      trialPurpose: ["prevention"],
      searchTitle: "myeloma",
    });
    expect(a.current().row).toEqual({
      trialPurpose: ["prevention"],
      searchTitle: "myeloma",
    });
  });

  it("measures the edit against what was believed WHEN it was made", async () => {
    // The unknown-version path refreshes `stored` before the write. Measuring
    // the delta after that refresh reads another tab's change as this
    // reader's edit, and the retry re-applies it.
    const a = versionedAdapter({ sponsor: "Acme" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    // Leave the version unknown, which sends the next save through the
    // refresh path.
    const versioning = a.methods.preferenceVersioning;
    const realWrite = versioning.write;
    versioning.write = async (filters, precondition) => {
      await realWrite(filters, precondition);
      return null;
    };
    await t.save({ sponsor: "Acme" });
    versioning.write = realWrite;

    // Another tab changes the sponsor the reader is merely displaying.
    a.elsewhere({ sponsor: "Other" }, '"v-other"');
    await t.save({ sponsor: "Acme", searchTitle: "myeloma" });

    expect(a.current().row).toEqual({ sponsor: "Other", searchTitle: "myeloma" });
  });

  it("still re-applies a value the reader genuinely changed", async () => {
    // The other side of the same rule: a field the reader DID edit must
    // survive the retry even when the other tab touched the same key — and
    // it has to be asserted with a FULL-panel payload, because a single-key
    // one is the shape that hid the original bug.
    const a = versionedAdapter({ sponsor: "Acme", phase: "PHASE3" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    a.elsewhere({ sponsor: "Other", phase: "PHASE3" }, '"v-other"');
    // `phase` is merely on screen; `sponsor` is the one the reader retyped.
    await t.save({ sponsor: "Mine", phase: "PHASE3" });

    expect(a.current().row).toEqual({ sponsor: "Mine", phase: "PHASE3" });
  });

  it("re-applies a clear the reader made, over the other tab's row", async () => {
    const a = versionedAdapter({ sponsor: "Acme", phase: "PHASE3" }, '"v0"');
    const t = adapterPreferences(a.methods);
    await t.get();

    // The other tab CHANGED the field this reader is clearing, which is what
    // makes the clear a real instruction rather than a no-op: pre-fix the
    // whole panel went back, taking `country` with it.
    a.elsewhere({ sponsor: "Other", country: "US" }, '"v-other"');
    // The panel hands over everything it owns; `sponsor` is the cleared one.
    await t.save({ sponsor: undefined, phase: "PHASE3" });

    // `sponsor` cleared because the reader cleared it. `country` kept because
    // the other tab added it. `phase` NOT restored: the other tab removed it
    // and this reader never touched it, so its removal stands — the same rule
    // read from the other side.
    expect(a.current().row).toEqual({ country: "US" });
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

describe("PreferenceWriter — a write that outlived the reader who made it (#583)", () => {
  // Same fact as `patientWriter`: EXACT keys the row on the bearer token, so
  // a payload sent under somebody else's credential lands in their row. The
  // saved-filters queue is simpler — one whole value, last-write-wins — so
  // there is nothing to partition; a write either belongs to whoever is
  // signed in at send time or it does not go.
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const settleAll = async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  };

  it("does not save a filter chosen by somebody else", async () => {
    const t = controllable();
    const onError = vi.fn();
    let who = "sub:iss|one";
    const w = new PreferenceWriter(t.transport, { identity: () => who, onError });

    w.save({ country: "US" });
    who = "sub:iss|two"; // the credential swaps during the debounce
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await settleAll();

    expect(t.calls).toEqual([]);
    expect(onError).toHaveBeenCalledTimes(1);
    expect((onError.mock.calls[0][0] as { name: string }).name).toBe("IdentityChanged");
  });

  it("does not clear somebody else's row when a Reset outlives its reader", async () => {
    // A reset is not a lesser write: it EMPTIES the row. Landing in the wrong
    // one destroys a saved search rather than merely adding to it, so the
    // click captures the identity exactly as a keystroke does.
    const t = controllable();
    const onError = vi.fn();
    let who = "sub:iss|one";
    const w = new PreferenceWriter(t.transport, { identity: () => who, onError });

    // Held behind an in-flight save so the reset waits in `pending` and is
    // run later, which is where the captured value has to survive to.
    w.save({ country: "US" });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    expect(t.calls).toEqual([{ kind: "save", value: { country: "US" } }]);

    w.reset();
    who = "sub:iss|two";
    await t.settle();
    await settleAll();

    expect(t.calls).toEqual([{ kind: "save", value: { country: "US" } }]);
    expect(
      onError.mock.calls.some((c) => (c[0] as { name?: string })?.name === "IdentityChanged"),
    ).toBe(true);
  });

  it("does not wedge the queue behind a stranded write", async () => {
    // The stranded write must not take the queue with it: the new reader is
    // entitled to save their own filters immediately afterwards. This is why
    // the refusal goes through the same tail as every other failure.
    const t = controllable();
    let who = "sub:iss|one";
    const w = new PreferenceWriter(t.transport, { identity: () => who, onError: () => {} });

    w.save({ country: "US" });
    who = "sub:iss|two";
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await settleAll();
    expect(t.calls).toEqual([]);

    // The new reader's own edit goes out normally.
    w.save({ country: "CA" });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await settleAll();
    expect(t.calls).toEqual([{ kind: "save", value: { country: "CA" } }]);
  });

  it("keeps a queued save under the reader who made it, not the one who flushes", async () => {
    // A save that waits in `pending` behind an in-flight request is sent from
    // the tail, and it must still be compared against the identity it was
    // MADE under. Storing only the value would let the queue re-attribute it
    // to whoever happens to be signed in when the wire clears.
    const t = controllable();
    const onError = vi.fn();
    let who = "sub:iss|one";
    const w = new PreferenceWriter(t.transport, { identity: () => who, onError });

    w.save({ country: "US" });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    expect(t.calls).toHaveLength(1); // on the wire

    w.save({ country: "US", distance: 50 }); // queued behind it, by user one
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    who = "sub:iss|two";
    await t.settle();
    await settleAll();

    expect(t.calls).toHaveLength(1); // the queued one never left
    expect(
      onError.mock.calls.some((c) => (c[0] as { name?: string })?.name === "IdentityChanged"),
    ).toBe(true);
  });

  it("lets a rotated credential through", async () => {
    const t = controllable();
    const onError = vi.fn();
    const w = new PreferenceWriter(t.transport, {
      identity: () => "sub:iss|one",
      onError,
    });

    w.save({ country: "US" });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await settleAll();

    expect(t.calls).toEqual([{ kind: "save", value: { country: "US" } }]);
    expect(onError).not.toHaveBeenCalled();
  });

  it("does nothing at all when nobody told it who is signed in", async () => {
    const t = controllable();
    const w = new PreferenceWriter(t.transport);

    w.save({ country: "US" });
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await settleAll();

    expect(t.calls).toEqual([{ kind: "save", value: { country: "US" } }]);
  });
});

describe("PreferenceWriter — the credential is read afresh at send (#583)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("does not trust a cache that nothing refreshed", async () => {
    // The cached reading only changes when somebody fetches a token, and
    // between an edit and its flush there may be no request at all. Against
    // the cache alone the swap below is invisible and the write goes out
    // under the new credential — which was the state of the first version of
    // this guard, measured.
    const t = controllable();
    const onError = vi.fn();
    const cached = "sub:iss|one"; // never updated, as in the failing shape
    let current = "sub:iss|one";
    const w = new PreferenceWriter(t.transport, {
      identity: () => cached,
      identityNow: async () => current,
      onError,
    });

    w.save({ country: "US" });
    current = "sub:iss|two";
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(t.calls).toEqual([]);
    expect(
      onError.mock.calls.some((c) => (c[0] as { name?: string })?.name === "IdentityChanged"),
    ).toBe(true);
  });

  it("still sends in the same task when the reader answers synchronously", async () => {
    // A contract this queue already had: `reset` jumps the debounce, and a
    // caller that advances its timers and reads `calls` is entitled to see
    // the request. Awaiting unconditionally moved every send a microtask
    // later and broke nine tests in this file.
    const t = controllable();
    const w = new PreferenceWriter(t.transport, { identity: () => "sub:iss|one" });

    w.reset();
    expect(t.calls).toEqual([{ kind: "reset" }]);
  });
});

describe("PreferenceWriter — only the cached reader (#583)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("falls back to it rather than to nothing", async () => {
    // Same reachable combination as the patient queue, same reason: a reader
    // that always answers `undefined` reads as "unknown", which is a match,
    // so the guard would be silently off.
    const t = controllable();
    const onError = vi.fn();
    let who = "sub:iss|one";
    const w = new PreferenceWriter(t.transport, { identity: () => who, onError });

    w.save({ country: "US" });
    who = "sub:iss|two";
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    await Promise.resolve();
    await Promise.resolve();

    expect(t.calls).toEqual([]);
    expect(
      onError.mock.calls.some((c) => (c[0] as { name?: string })?.name === "IdentityChanged"),
    ).toBe(true);
  });
});

describe("adapterPreferences — a belief belongs to the reader it was read for (#603)", () => {
  // The transport caches what the server holds, so a save can send the
  // reader's payload merged over it — without that, a partial payload
  // replaces the row and everything the reader did not submit is gone.
  //
  // Which row it is caching depends on the credential. In the shapes #583
  // measured, the account changes and no key moves, so the transport
  // survives the swap holding the PREVIOUS reader's filters, and the next
  // reader's first save merges over them.
  //
  // #583's own guard cannot see this: that one compares the credential a
  // write was queued under against the one it is sent under, and here they
  // agree. What is wrong is the merge base.
  function fakeRow() {
    const saved: Array<Record<string, unknown>> = [];
    const reads: string[] = [];
    let row: Record<string, unknown> = {};
    return {
      saved,
      reads,
      put: (v: Record<string, unknown>) => {
        row = v;
      },
      methods: {
        getPreferences: async () => {
          reads.push("get");
          return row as never;
        },
        savePreferences: async (f: never) => {
          saved.push(f as Record<string, unknown>);
          row = f as Record<string, unknown>;
        },
        resetPreferences: async () => {
          row = {};
        },
      },
    };
  }

  it("does not merge one reader's filters into the next reader's row", async () => {
    const a = fakeRow();
    let who = "sub:iss|one";
    const t = adapterPreferences(a.methods, () => who, async () => who);

    // Reader one's row, read and cached.
    a.put({ country: "US", distance: 50 });
    expect(await t.get()).toEqual({ country: "US", distance: 50 });

    // The account changes with nothing signalled, and the new reader's row
    // holds something else.
    who = "sub:iss|two";
    a.put({ country: "CA" });
    await t.save({ distance: 100 });

    // The payload is two's row plus two's edit. One's `country: US` must not
    // be in it — that is somebody else's saved search.
    expect(a.saved.at(-1)).toEqual({ country: "CA", distance: 100 });
    expect(a.saved.at(-1)).not.toHaveProperty("country", "US");
  });

  it("re-reads for the new reader rather than writing blind", async () => {
    // Forgetting is only half: `seeded` has to go with it, or the transport
    // believes an empty row and replaces the new reader's filters with the
    // one key they touched.
    const a = fakeRow();
    let who = "sub:iss|one";
    const t = adapterPreferences(a.methods, () => who, async () => who);
    a.put({ country: "US" });
    await t.get();
    const before = a.reads.length;

    who = "sub:iss|two";
    a.put({ country: "CA", distance: 50 });
    await t.save({ distance: 100 });

    expect(a.reads.length).toBeGreaterThan(before);
    expect(a.saved.at(-1)).toEqual({ country: "CA", distance: 100 });
  });

  it("keeps the cache across a token refresh, which is the common case", async () => {
    // Tokens rotate hourly. Dropping the cache on every rotation would cost
    // a read per save for every reader, all day, to protect against a swap
    // that is rare — and `fingerprintOf` exists precisely so a refresh is
    // not a change.
    //
    // BOTH halves of the reader contract, where this used to pass only
    // the cached one. That matters because the single read asserted here
    // is what the full contract buys: a host that cannot be asked cannot
    // tell a rotation from a swap at all, so it gets no guard and no
    // extra reads either — pinned by `does not guard, and does not
    // re-read, a host that cannot be asked`, below.
    const a = fakeRow();
    const t = adapterPreferences(a.methods, () => "sub:iss|one", () => "sub:iss|one");
    a.put({ country: "US" });
    await t.get();
    const before = a.reads.length;

    await t.save({ distance: 100 });

    expect(a.reads.length).toBe(before);
    expect(a.saved.at(-1)).toEqual({ country: "US", distance: 100 });
  });

  it("keeps the cache when nobody can say who is signed in", async () => {
    // The local no-auth stand, and any deployment behind a gateway that
    // injects the header. Re-reading on every call would slow down exactly
    // the host this guard can do nothing for.
    const a = fakeRow();
    const t = adapterPreferences(a.methods, () => undefined);
    a.put({ country: "US" });
    await t.get();
    const before = a.reads.length;

    await t.save({ distance: 100 });

    expect(a.reads.length).toBe(before);
    expect(a.saved.at(-1)).toEqual({ country: "US", distance: 100 });
  });

  it("does nothing at all when nobody told it who is signed in", async () => {
    // The default, and every caller that has not been wired. Written as a
    // swap that is NOT noticed, because the merge assertion alone passed
    // both with the change reverted and with the guard forced to fire on
    // every call — it measured neither direction.
    const a = fakeRow();
    const t = adapterPreferences(a.methods); // no identity reader
    a.put({ country: "US" });
    await t.get();
    const before = a.reads.length;

    // Whatever the credential is doing, this transport was not told and
    // must behave exactly as it did: no re-read, and the cache still used.
    a.put({ country: "CA" });
    await t.save({ distance: 100 });

    expect(a.reads.length).toBe(before);
    expect(a.saved.at(-1)).toEqual({ country: "US", distance: 100 });
  });

  it("does not let a read issued for the old reader seed the new one", async () => {
    // The generation bump, and it has to be timed to mean anything. If the
    // old read RESOLVES before the swap it has already seeded, and the
    // clear alone covers it — the first version of this test was arranged
    // that way and passed with the bump removed. What the bump is for is a
    // read that is still in the air when the new reader's save has already
    // cleared the cache: it resolves afterwards holding the PREVIOUS
    // reader's row, and must not seed from it.
    const a = fakeRow();
    let who = "sub:iss|one";
    let releaseFirst: () => void = () => {};
    const firstOut = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let call = 0;
    const slow = {
      ...a.methods,
      getPreferences: async () => {
        call += 1;
        a.reads.push("get");
        if (call === 1) {
          await firstOut;
          return { country: "US" } as never;
        }
        return { country: "CA" } as never;
      },
    };
    const t = adapterPreferences(slow, () => who, async () => who);

    const inFlight = t.get();
    await Promise.resolve(); // the read is out, issued for reader one
    who = "sub:iss|two";

    // The new reader saves. It serialises behind the read already in
    // flight, which lands holding reader ONE's row — and that read is now
    // DROPPED rather than adopted, because the reader it went out for is no
    // longer the one signed in (`stillTheirs`). Nothing is seeded, so this
    // save takes the re-read path and builds against reader TWO's own row.
    //
    // THE EXPECTATION CHANGED HERE, and deliberately. It used to be that
    // the read seeded, was stamped as reader one's, and the post-await
    // check refused the save — costing the reader an edit to protect
    // somebody else's row. Dropping the read instead protects the same row
    // and costs nothing: the edit lands where it belongs on the first
    // attempt. The refusal still exists for the case it is the only answer
    // to — a swap after the payload was composed — and
    // `preferences.whoseRequest.test.ts` pins the other half.
    const saving = t.save({ distance: 100 });
    await Promise.resolve();
    releaseFirst();
    await inFlight;
    await saving;
    expect(a.saved.at(-1)).toEqual({ country: "CA", distance: 100 });
    // The whole point: nothing of reader one's crossed over.
    expect(a.saved.at(-1)).not.toHaveProperty("country", "US");

    // And the next one goes the same way.
    await t.save({ distance: 200 });
    expect(a.saved.at(-1)).toEqual({ country: "CA", distance: 200 });
  });

  it("does not guard, and does not re-read, a host that cannot be asked", async () => {
    // THIS TEST REPLACES its opposite, twice over, and the history is the
    // point of keeping it.
    //
    // It first asserted that a cache stamped "unknown" survives the
    // identity becoming known — which was the hole the mount defect went
    // through. It was then changed to assert the reverse, re-reading to be
    // safe. Both were attempts to half-protect a host that supplies
    // `credentialIdentity` and not `credentialIdentityNow`, and half was
    // the wrong amount: measured, that shape leaked in full anyway,
    // because the transport decided it could ask by looking at whether an
    // argument was passed and `hooks.ts` always passed one.
    //
    // The decision now is that the shape is not supported. Asking means
    // asking; a host that cannot be asked gets no guard, pays for no extra
    // reads, and `hooks.ts` reports the missing prop as missing instead of
    // substituting the cache for it. What is asserted here is that "off"
    // is all that happens — no refusals, no re-reads, the reader's own row
    // loaded and merged as it always was.
    const a = fakeRow();
    let who: string | undefined = undefined;
    const t = adapterPreferences(a.methods, () => who);
    a.put({ country: "US" });
    await t.get();
    const before = a.reads.length;

    who = "sub:iss|one";
    await t.save({ distance: 100 });

    expect(a.reads.length).toBe(before);
    expect(a.saved.at(-1)).toEqual({ country: "US", distance: 100 });
  });

  it("does not re-read for a rotated token, which is what `sameIdentity` is for", async () => {
    // The case the test above used to be standing in for, pinned properly:
    // a STAMPED cache meeting the same reader again. Firebase hands out a
    // fresh JWT about once an hour, and a strict comparison would throw
    // away a good cache — one wasted read per reader, per hour.
    const a = fakeRow();
    let who = "sub:iss|one";
    const t = adapterPreferences(a.methods, () => who, () => who);
    a.put({ country: "US" });
    await t.get();
    const before = a.reads.length;

    // Same person, a credential the host has since refreshed. The
    // fingerprint does not move, so nothing here should notice.
    who = "sub:iss|one";
    await t.save({ distance: 100 });

    expect(a.reads.length).toBe(before);
    expect(a.saved.at(-1)).toEqual({ country: "US", distance: 100 });
  });
});
