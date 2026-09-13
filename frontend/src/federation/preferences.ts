// Saved search filters: where they are kept, and how writes are sequenced.
//
// The filter panel writes on every change. Two things make that harder than
// it looks, and CB earned both the hard way:
//
//   * Ticking two checkboxes in one frame issues two writes. Without
//     sequencing they can land out of order and the server keeps the older
//     one — the panel then shows a filter the backend does not have.
//   * Reset has to win. A save already on the wire when Reset is pressed
//     would otherwise complete afterwards and restore what was just cleared.
//
// So writes go through a queue: one request in flight, at most one write
// waiting behind it (a newer one replaces the waiting one — nobody needs the
// intermediate states), and a generation counter that retires anything from
// before a Reset. Debouncing sits in front of that to keep the common case —
// dragging a slider — down to one request.
//
// The transport is injected. With a `TrialStateAdapter` it is PROMOP; with no
// adapter at all it is `localStorage`, so the dev harness and any host that
// supplies no state still keep filters across a reload instead of losing the
// feature.

import type { TrialStateAdapter } from "./state";
import type { FilterState } from "./types";

/** Just the preference half of `TrialStateAdapter`.
 *
 *  Narrowed on purpose: the caller holds the adapter behind a ref (it can be
 *  rebuilt every render), so it passes three bound functions rather than the
 *  object — and asking for the whole interface would force a cast that claims
 *  more than is true. */
export type PreferenceMethods = Pick<
  TrialStateAdapter,
  "getPreferences" | "savePreferences" | "resetPreferences"
>;

/** How long to coalesce rapid edits. Long enough to swallow a slider drag,
 *  short enough that a deliberate change feels saved. */
export const FILTER_DEBOUNCE_MS = 400;

export interface PreferenceTransport {
  get(): Promise<FilterState>;
  save(filters: FilterState): Promise<void>;
  reset(): Promise<void>;
}

/** PROMOP, through the host-supplied state adapter.
 *
 *  The endpoint does NOT merge key by key, which is what this module was
 *  originally written for. `preferences` is a single JSON column and
 *  `TrialSearchPreferencesSerializer` has no custom `update`, so DRF assigns
 *  the whole dict: a PATCH of `{"a": 1}` followed by `{"b": 2}` leaves the row
 *  holding only `b`. Measured, both in PROMOP's serializer and against DRF's
 *  `partial=True` directly, because the whole design turns on it.
 *
 *  Two consequences, and the second is why this matters:
 *
 *  - sending `null` for a key that has gone away is pointless. Under a
 *    wholesale replace a key disappears by being absent, and `FilterPanel`
 *    represents a cleared control as `undefined`, which JSON drops for us.
 *  - every write must carry the COMPLETE set, or it deletes what it omits.
 *    So the transport remembers what the server holds and merges the
 *    reader's payload over it. The case that needs this is the one where the
 *    caller's set is a strict subset through no fault of its own: a slow load
 *    that arrived after the reader had already edited, and was dropped rather
 *    than revert their edit. Their saved sponsor and phase are still their
 *    preferences — the panel simply never showed them — and a save that
 *    omits them is not a clear, it is a loss.
 *
 *  A filter the reader DID clear still clears, because `userOwnedFilters`
 *  emits it present-and-`undefined`: the merge keeps the key, JSON drops it,
 *  and the stored dict comes back without it.
 */
export function adapterPreferences(
  state: PreferenceMethods,
): PreferenceTransport {
  // What the server is believed to hold — seeded by `get`, kept current by
  // every successful write. Under a wholesale replace this is not an
  // optimisation: it is the only thing standing between a partial payload and
  // the rest of the reader's saved filters.
  let stored: FilterState = {};
  // Whether that belief rests on anything. An empty `stored` because the read
  // came back empty and an empty `stored` because the read FAILED are the same
  // value and opposite situations: the first means there is nothing to
  // protect, the second means we have no idea what we would be overwriting.
  // Writing in the second case replaces the row with whatever the reader
  // happens to be holding.
  let seeded = false;
  // Bumped by `reset`. A read in flight when the reader presses Reset resolves
  // afterwards holding the values they just cleared, and seeding from it puts
  // them in the next payload — so the row comes back. The generation says
  // which era a read belongs to.
  let generation = 0;
  // The first read, so a save cannot overtake it. A reader who edits inside
  // the debounce window on a slow connection would otherwise PATCH before
  // `stored` was seeded — and under a wholesale replace that payload IS the
  // row, so everything they had saved would go with it.
  let firstRead: Promise<unknown> | null = null;
  //
  // NOT handled here, on purpose: clearing a filter the HOST seeded via
  // `initialFilters` does not persist across a remount. The host's seed is its
  // scope for that mount and reasserts itself — which is the same reason the
  // baseline is excluded from what gets saved at all. Representing "the reader
  // cleared the host's value" would need a sentinel that survives reads, and
  // that is a product decision about whose scope wins, not a defect.
  return {
    get: async () => {
      const era = generation;
      const read = (async () => {
        const fromServer = (await state.getPreferences()) ?? {};
        const out: FilterState = {};
        for (const [k, v] of Object.entries(fromServer)) {
          // A null is a key an older build cleared by writing one, not a set
          // value. Nothing writes them any more.
          if (v !== null && v !== undefined) (out as Record<string, unknown>)[k] = v;
        }
        if (era === generation) {
          stored = { ...out };
          seeded = true;
        }
        return out;
      })();
      firstRead = read;
      return read;
    },
    save: async (filters) => {
      // Serialised behind the first read — see `firstRead` above. A save that
      // overtakes it would build its payload against an empty `stored` and
      // replace the row with the one key the reader has touched.
      if (firstRead) await firstRead.catch(() => {});
      if (!seeded) {
        // One more attempt, because a read that failed once may not fail
        // twice, and the alternative is losing the reader's edit.
        const era = generation;
        try {
          const retry = (await state.getPreferences()) ?? {};
          const out: FilterState = {};
          for (const [k, v] of Object.entries(retry)) {
            if (v !== null && v !== undefined) (out as Record<string, unknown>)[k] = v;
          }
          // Same era check as `get`: a Reset during the retry wins.
          if (era === generation) {
            stored = out;
            seeded = true;
          }
        } catch {
          // Still blind. Refusing costs the reader this one edit, which they
          // can make again; writing costs them every filter they ever saved,
          // which they cannot. `PreferenceWriter` reports it through
          // `onError` like any other failed write.
          throw new Error("saved filters could not be read, so they were not overwritten");
        }
      }
      // The reader's payload over what the server holds. A key they cleared is
      // present-and-`undefined` and drops out on the way through JSON; a key
      // they never saw survives.
      const payload: Record<string, unknown> = { ...stored };
      for (const [k, v] of Object.entries(filters)) {
        if (v === undefined) delete payload[k];
        else payload[k] = v;
      }
      await state.savePreferences(payload as FilterState);
      // AFTER it resolves. Recording a payload that failed means the next one
      // is built against a row that does not exist.
      stored = { ...payload } as FilterState;
    },
    reset: async () => {
      generation += 1;
      // `stored` cleared REGARDLESS of the outcome, which is the opposite of
      // what a merging endpoint would want. Under a replace, a `stored` still
      // full of the old values means the next save puts them all back — the
      // reader watches their filters disappear and finds them again on the
      // next mount. Clearing first means the next save writes only what they
      // are holding, which repairs a reset that failed.
      //
      // `seeded` is NOT claimed here, though. A reset that fails on a
      // transport that never read the row leaves us knowing nothing about it
      // while believing we know it is empty, and the next save replaces it
      // blind — the very thing `save` refuses to do. A reset that fails after
      // a good read is different: the old contents are still known, and
      // writing only what the reader holds is the repair.
      const knew = seeded;
      stored = {};
      seeded = knew;
      await state.resetPreferences();
      seeded = true;
    },
  };
}

/** The no-adapter fallback.
 *
 *  Per-browser rather than per-person, which is the honest limit of storage
 *  the host did not give us: it is keyed by patient so two patients in one
 *  browser do not share filters, but it does not follow the patient to
 *  another device. Losing the feature entirely would be worse — the panel
 *  would silently forget on every reload.
 */
export function localStoragePreferences(key: string): PreferenceTransport {
  // Hashed, because `key` is the patient key — for an inline `patientInfo`
  // that is the serialized payload itself (disease, country, labs, dates).
  // It was a react-query cache key living in memory; putting it in a storage
  // key would leave a patient record on disk, per browser, unexpired, for
  // every patient ever viewed, since `reset` only removes the current one.
  const storageKey = `exact.filters.${hashKey(key)}`;
  const storage = (): Storage | null => {
    try {
      // Absent in SSR, and throws outright in a sandboxed iframe or with
      // site data blocked. Either way the caller gets the in-memory default.
      return typeof localStorage === "undefined" ? null : localStorage;
    } catch {
      return null;
    }
  };
  const readRaw = (store: Storage): Record<string, unknown> => {
    try {
      const parsed: unknown = JSON.parse(store.getItem(storageKey) ?? "");
      return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
    } catch {
      // Absent, or a corrupt entry: a half-written value, or a shape from an
      // older build. Treat it as "no saved filters" rather than throwing on
      // mount and taking the list down with it.
      return {};
    }
  };
  return {
    get: async () => {
      const store = storage();
      if (!store) return {};
      const out: FilterState = {};
      for (const [k, v] of Object.entries(readRaw(store))) {
        // A null is a key the reader cleared, not a set one.
        if (v !== null && v !== undefined) (out as Record<string, unknown>)[k] = v;
      }
      return out;
    },
    save: async (filters) => {
      try {
        const store = storage();
        if (!store) return;
        // Merged over what is on disk, with an explicit null for a cleared
        // key. Same OUTCOME as the adapter, reached differently: the adapter
        // merges in memory and sends a complete dict because the server
        // replaces; this one merges on disk because it is the storage. A
        // wholesale overwrite here would delete keys the caller never knew
        // about — exactly the state a deliberately-discarded load leaves it
        // in. `get` strips the nulls again.
        const existing = readRaw(store);
        for (const [k, v] of Object.entries(filters)) {
          existing[k] = v === undefined ? null : v;
        }
        store.setItem(storageKey, JSON.stringify(existing));
      } catch {
        // Quota, or private mode. A filter that fails to persist is not a
        // reason to break the search that is already on screen.
      }
    },
    reset: async () => {
      try {
        storage()?.removeItem(storageKey);
      } catch {
        /* as above */
      }
    },
  };
}

/** A short stable digest of the patient key. djb2 — not a security boundary,
 *  just enough that a storage key is not a readable patient record. */
function hashKey(key: string): string {
  let h = 5381;
  for (let i = 0; i < key.length; i += 1) h = ((h << 5) + h + key.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

/** What is waiting behind the request in flight.
 *
 *  Two fields rather than one slot, because a reset and a save are not
 *  alternatives: if the reader presses Reset and then changes a filter, BOTH
 *  have to happen and in that order. Collapsing them into one slot let the
 *  later save overwrite the reset — and the reset is what empties the row and
 *  clears the transport's memory of it, so dropping it leaves the next save
 *  writing the old values back alongside the new edit.
 */
interface Pending {
  reset: boolean;
  save: FilterState | null;
}

/** One unit of work for the queue. */
type Write = { kind: "save"; value: FilterState } | { kind: "reset" };

export interface PreferenceWriterOptions {
  debounceMs?: number;
  /** Reported instead of thrown: a filter that failed to save must not take
   *  down the list that is already rendered. */
  onError?: (error: unknown) => void;
  /** Called after a write lands. The counterpart to `onError`: without it a
   *  caller showing "couldn't save" has no way to learn that the NEXT write
   *  succeeded, and goes on saying it. */
  onSuccess?: () => void;
}

/**
 * Debounce in front, a one-deep queue behind.
 *
 * `save` collapses rapid edits into one request. While a request is in
 * flight a further `save` does not start a second one — it replaces whatever
 * was waiting, because only the newest filter state matters. `reset` jumps
 * the debounce (it is a deliberate click, not a drag) and invalidates every
 * write issued before it.
 */
export class PreferenceWriter {
  private readonly transport: PreferenceTransport;
  private readonly debounceMs: number;
  private readonly onError: (error: unknown) => void;
  private readonly onSuccess: () => void;

  private timer: ReturnType<typeof setTimeout> | null = null;
  private debounced: FilterState | null = null;
  private inFlight: Promise<void> | null = null;
  private pending: Pending = { reset: false, save: null };
  /** Bumped by `reset`. A write started under an older generation has its
   *  result ignored, and a queued one is dropped. */
  private generation = 0;

  constructor(transport: PreferenceTransport, opts: PreferenceWriterOptions = {}) {
    this.transport = transport;
    this.debounceMs = opts.debounceMs ?? FILTER_DEBOUNCE_MS;
    this.onSuccess = opts.onSuccess ?? (() => {});
    this.onError =
      opts.onError ??
      ((error: unknown) => {
        // Not silence by default. A persistently failing adapter otherwise
        // presents as "filters just don't save", with nothing anywhere to say
        // why — and this is a best-effort write, so nothing else reports it.
        console.warn("[exact] saved filters could not be written", error);
      });
  }

  /** Persist these filters, eventually. */
  save(filters: FilterState): void {
    this.debounced = filters;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      const value = this.debounced;
      this.debounced = null;
      if (value !== null) this.enqueue({ kind: "save", value });
    }, this.debounceMs);
  }

  /** Clear them, now, and retire anything already on the wire. */
  reset(): void {
    this.cancelDebounce();
    this.generation += 1;
    this.enqueue({ kind: "reset" });
  }

  /** Send a pending debounced write immediately. For unmount: a filter
   *  changed in the last few hundred milliseconds should not be lost because
   *  the user navigated away. */
  flush(): void {
    if (this.timer === null) return;
    // Read before cancelling: `cancelDebounce` clears the pending value too,
    // so taking it afterwards always reads null and flush sends nothing.
    const value = this.debounced;
    this.cancelDebounce();
    if (value !== null) this.enqueue({ kind: "save", value });
  }

  /** Resolves when nothing is in FLIGHT. A write still sitting in the debounce
   *  is not covered — call `flush()` first if you need that too, which is what
   *  a host awaiting a save before navigating wants. */
  async settled(): Promise<void> {
    while (this.inFlight) await this.inFlight;
  }

  private cancelDebounce(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.debounced = null;
  }

  private enqueue(write: { kind: "save"; value: FilterState } | { kind: "reset" }): void {
    if (this.inFlight) {
      if (write.kind === "reset") {
        // A reset supersedes a queued save — that save was issued before it.
        this.pending.reset = true;
        this.pending.save = null;
      } else if (this.pending.reset) {
        // Waits behind the reset, not instead of it.
        this.pending.save = write.value;
      } else {
        // Only the latest save matters; the intermediate states do not.
        this.pending.save = write.value;
      }
      return;
    }
    void this.run(write);
  }

  private async run(write: Write): Promise<void> {
    const generation = this.generation;
    let request: Promise<void>;
    try {
      request =
        write.kind === "reset"
          ? this.transport.reset()
          : this.transport.save(write.value);
    } catch (error: unknown) {
      // A host adapter that throws synchronously rather than rejecting. Left
      // outside the chain it bypassed `onError`, never assigned `inFlight`,
      // and surfaced only as an unhandled rejection — while the class
      // promises that a failed write is reported, not thrown.
      //
      // Rejected rather than reported here and resolved: a resolved
      // substitute travels the SUCCESS path below, so the failure would be
      // reported and then immediately announced as a success. One error path,
      // and it is the chain's.
      request = Promise.reject(error);
    }

    this.inFlight = request
      .then(() => {
        this.onSuccess();
      })
      .catch((error: unknown) => {
        this.onError(error);
      })
      .then(() => {
        this.inFlight = null;
        if (this.pending.reset) {
          this.pending.reset = false;
          void this.run({ kind: "reset" });
          return;
        }
        const save = this.pending.save;
        this.pending.save = null;
        if (save === null) return;
        // Drop a save issued before a reset: sending it now would restore
        // exactly what the reset cleared.
        if (generation !== this.generation) return;
        void this.run({ kind: "save", value: save });
      });
  }
}
