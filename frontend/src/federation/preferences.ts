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
  /** "You no longer know what is stored — do not delete what you did not
   *  write." Called when a load is read but deliberately NOT applied, which
   *  leaves the caller holding a set that is a strict subset of what is
   *  stored. Without it the next save reads as "these are all the filters
   *  there are" and clears the rest. */
  forget(): void;
}

/** PROMOP, through the host-supplied state adapter.
 *
 *  The endpoint MERGES a partial update, and `FilterPanel` represents a
 *  cleared control as `undefined` — which JSON drops. Left alone, clearing a
 *  filter would leave the old value on the server and it would come back on
 *  the next mount. So a key that was in the last payload and is gone from this
 *  one is sent explicitly as `null`, which the merge does overwrite, and
 *  `get` strips those nulls again so a cleared filter does not read back as a
 *  set one.
 */
export function adapterPreferences(
  state: PreferenceMethods,
): PreferenceTransport {
  // What the server is believed to hold. Seeded by `get`, not just by our own
  // writes: clearing a filter that came FROM the server has to send that key
  // as null too, and with an empty starting set the payload would simply omit
  // it and the merge would keep the old value.
  let lastSent: FilterState = {};
  // The first read, so a save cannot overtake it. A reader who edits inside
  // the debounce window on a slow connection would otherwise PATCH before
  // `lastSent` was seeded, and the keys the server already held would survive
  // the merge and reappear on the next mount.
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
      const read = (async () => {
          const stored = (await state.getPreferences()) ?? {};
        const out: FilterState = {};
        for (const [k, v] of Object.entries(stored)) {
          if (v !== null && v !== undefined) (out as Record<string, unknown>)[k] = v;
        }
        lastSent = { ...out };
        return out;
      })();
      firstRead = read;
      return read;
    },
    save: async (filters) => {
      // Serialised behind the first read — see `firstRead` above.
      if (firstRead) await firstRead.catch(() => {});
      const payload: Record<string, unknown> = {};
      for (const k of Object.keys(lastSent)) payload[k] = null;
      for (const [k, v] of Object.entries(filters)) {
        payload[k] = v === undefined ? null : v;
      }
      await state.savePreferences(payload as FilterState);
      // AFTER it resolves, like `reset`. Recording the new set for a PATCH
      // that failed means the next payload stops nulling the keys this one
      // was meant to clear, and they come back on the next mount.
      lastSent = { ...filters };
    },
    reset: async () => {
      await state.resetPreferences();
      // AFTER it resolves. Clearing the seed first means a failed reset leaves
      // us believing the server is empty, so the next save nulls nothing — and
      // the filters the reader watched disappear come back on the next mount.
      lastSent = {};
    },
    forget: () => {
      lastSent = {};
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
        // Merged over what is there, with an explicit null for a cleared key
        // — the same shape the adapter sends, and for the same reason. A
        // wholesale overwrite would delete keys the caller never knew about,
        // which is exactly the state a deliberately-discarded load leaves it
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
    // Nothing to forget: every write already merges, so this transport never
    // deletes a key it was not told about.
    forget: () => {},
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
 *  later save overwrite the reset, so the partial update already on the server
 *  was never cleared and stale keys survived alongside the new edit.
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
      this.onError(error);
      request = Promise.resolve();
    }

    this.inFlight = request
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
