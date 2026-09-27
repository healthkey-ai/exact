import { describe, expect, it, vi } from "vitest";

import { PatientFieldWriter } from "./patientWriter";
import type { WriteOutcome } from "./state";

/** A transport that refuses the batch, naming fields the way PROMOP does. */
function refusing(byCall: Array<string[] | null>) {
  const calls: Array<Record<string, unknown>> = [];
  let call = 0;
  const write = vi.fn(async (fields: Record<string, unknown>) => {
    calls.push({ ...fields });
    const named = byCall[call] ?? null;
    call += 1;
    if (named === null) return {} as Record<string, WriteOutcome>;
    // The shape the adapter raises: `fields` is what the server objected to.
    throw Object.assign(new Error("refused"), { fields: named });
  });
  return { write, calls };
}

/** A transport whose answers the test hands out one at a time. */
function transport() {
  const calls: Array<Record<string, unknown>> = [];
  let release: ((outcomes: Record<string, WriteOutcome>) => void) | null = null;
  const write = vi.fn((fields: Record<string, unknown>) => {
    calls.push({ ...fields });
    return new Promise<Record<string, WriteOutcome>>((resolve) => {
      release = resolve;
    });
  });
  return {
    write,
    calls,
    /** Answer the request currently on the wire. */
    answer: (outcomes: Record<string, WriteOutcome> = {}) => {
      const settle = release;
      release = null;
      settle?.(outcomes);
      return Promise.resolve();
    },
    pending: () => release !== null,
  };
}

const saved = (value: unknown): WriteOutcome => ({ status: "saved", value });
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("batching", () => {
  it("sends two fields edited in one breath as one request", async () => {
    // Every write re-derives the projection and rescores the match, so three
    // gaps filled in a row should not cost three of each.
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("hemoglobin_g_dl", 12);
    w.save("platelet_count", 200);
    await new Promise((r) => setTimeout(r, 20));
    expect(t.write).toHaveBeenCalledTimes(1);
    expect(t.calls[0]).toEqual({ hemoglobin_g_dl: 12, platelet_count: 200 });
  });

  it("keeps only the last value for a field edited twice", async () => {
    // The intermediate value is one the reader had already moved on from;
    // writing it would put it in the record and rescore the match on it.
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("hemoglobin_g_dl", 12);
    w.save("hemoglobin_g_dl", 13);
    await new Promise((r) => setTimeout(r, 20));
    expect(t.calls[0]).toEqual({ hemoglobin_g_dl: 13 });
  });

  it("does not restart the wait each time a field is added", async () => {
    // A debounce that re-arms would hold the batch open for as long as the
    // reader keeps typing, which on a form of twenty rows is for ever.
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 30 });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    w.save("b", 2);
    await new Promise((r) => setTimeout(r, 20));
    expect(t.write).toHaveBeenCalledTimes(1);
    expect(t.calls[0]).toEqual({ a: 1, b: 2 });
  });
});

describe("one request at a time", () => {
  it("holds an edit made during a flight for the next batch", async () => {
    // Two PATCHes racing is not a performance question: each re-derives the
    // projection from the facts it just wrote, so the loser's derivation can
    // land last and describe a record that no longer exists.
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    expect(t.write).toHaveBeenCalledTimes(1);

    w.save("b", 2);
    await new Promise((r) => setTimeout(r, 20));
    expect(t.write).toHaveBeenCalledTimes(1);

    await t.answer({ a: saved(1) });
    await tick();
    expect(t.write).toHaveBeenCalledTimes(2);
    expect(t.calls[1]).toEqual({ b: 2 });
  });

  it("sends what arrived during a flight without waiting for another edit", async () => {
    // Left to the timer, the reader's last edit would sit there until they
    // made one more — which they have no reason to.
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    w.save("b", 2);
    await t.answer({ a: saved(1) });
    await tick();
    expect(t.calls[1]).toEqual({ b: 2 });
  });

  it("drains a queue that grew twice over during one flight", async () => {
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    w.save("b", 2);
    w.save("c", 3);
    await t.answer({ a: saved(1) });
    await tick();
    expect(t.calls[1]).toEqual({ b: 2, c: 3 });
  });
});

describe("what the caller is told", () => {
  it("reports every field in the batch, by name", async () => {
    const t = transport();
    const settled: Array<[string, string]> = [];
    const w = new PatientFieldWriter(t.write, {
      debounceMs: 5,
      onSettled: (field, outcome) => settled.push([field, outcome.status]),
    });
    w.save("a", 1);
    w.save("b", 2);
    await new Promise((r) => setTimeout(r, 20));
    await t.answer({ a: saved(1), b: { status: "differs", value: 9 } });
    await tick();
    expect(settled.sort()).toEqual([
      ["a", "saved"],
      ["b", "differs"],
    ]);
  });

  it("calls a field the answer skipped unconfirmed, not the whole batch", async () => {
    // The caller is owed a verdict for everything it sent, and one silent
    // field says nothing about the others.
    const t = transport();
    const settled: Record<string, string> = {};
    const w = new PatientFieldWriter(t.write, {
      debounceMs: 5,
      onSettled: (field, outcome) => {
        settled[field] = outcome.status;
      },
    });
    w.save("a", 1);
    w.save("b", 2);
    await new Promise((r) => setTimeout(r, 20));
    await t.answer({ a: saved(1) });
    await tick();
    expect(settled).toEqual({ a: "saved", b: "unconfirmed" });
  });

  it("reports a refusal against every field it was carrying", async () => {
    // A rejection does not say which field the server objected to, so the
    // caller cannot be told less than "all of these".
    const write = vi.fn(async () => {
      throw new Error("403");
    });
    const errors: string[][] = [];
    const w = new PatientFieldWriter(write, {
      debounceMs: 5,
      onError: (fields) => errors.push([...fields].sort()),
    });
    w.save("a", 1);
    w.save("b", 2);
    await new Promise((r) => setTimeout(r, 20));
    await tick();
    expect(errors).toEqual([["a", "b"]]);
  });

  it("keeps going after a refusal", async () => {
    // A failed batch must not wedge the queue: the next edit is a new claim,
    // and the reader has no way to retry other than by making it.
    let fail = true;
    const write = vi.fn(async () => {
      if (fail) throw new Error("boom");
      return {};
    });
    const w = new PatientFieldWriter(write, { debounceMs: 5, onError: () => {} });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    fail = false;
    w.save("b", 2);
    await new Promise((r) => setTimeout(r, 20));
    expect(write).toHaveBeenCalledTimes(2);
  });
});

describe("leaving", () => {
  it("flush sends what is waiting, without the timer", async () => {
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 10_000 });
    w.save("a", 1);
    expect(t.write).not.toHaveBeenCalled();
    w.flush();
    // One microtask, not the timer: the transport is called through
    // `Promise.resolve().then` so a synchronous throw cannot escape before
    // the queue knows a batch is on the wire.
    await tick();
    expect(t.calls[0]).toEqual({ a: 1 });
  });

  it("flush does not send a second request while one is in flight", async () => {
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    w.save("b", 2);
    w.flush();
    expect(t.write).toHaveBeenCalledTimes(1);
  });

  it("settled waits for the wire to clear", async () => {
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    let done = false;
    const waiting = w.settled().then(() => {
      done = true;
    });
    await tick();
    expect(done).toBe(false);
    await t.answer({ a: saved(1) });
    await waiting;
    expect(done).toBe(true);
  });

  it("settled does not send what is only waiting for the timer", async () => {
    // Named rather than assumed: an unmount wants `flush()` first, and a
    // `settled()` that quietly sent would make the difference invisible.
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 10_000 });
    w.save("a", 1);
    await w.settled();
    expect(t.write).not.toHaveBeenCalled();
  });
});

describe("what is still owed, continued", () => {
  it("names a field once when it is both on the wire and queued again", async () => {
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    w.save("a", 9);
    expect(w.outstanding).toEqual(["a"]);
  });
});

describe("a callback that throws", () => {
  it("does not wedge the queue when the post-write re-read throws", async () => {
    // It runs before the queue unlocks, so a throw there would leave every
    // later edit silently dropped and "Saving…" up for ever. In the page it
    // is three `invalidateQueries` calls.
    const t = transport();
    const w = new PatientFieldWriter(t.write, {
      debounceMs: 5,
      onBatchSettled: () => {
        throw new Error("boom");
      },
    });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    await t.answer({ a: saved(1) });
    await tick();

    w.save("b", 2);
    await new Promise((r) => setTimeout(r, 20));
    expect(t.write).toHaveBeenCalledTimes(2);
  });

  it("does not turn a success into a refusal when reporting one throws", async () => {
    // `.catch` is chained after the reporting, so an unguarded throw there
    // lands in `onError` — the row saying "couldn't save" over a value the
    // record does hold.
    const t = transport();
    const errors: string[][] = [];
    const w = new PatientFieldWriter(t.write, {
      debounceMs: 5,
      onSettled: () => {
        throw new Error("boom");
      },
      onError: (fields) => errors.push(fields),
    });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    await t.answer({ a: saved(1) });
    await tick();
    expect(errors).toEqual([]);
  });

  it("survives a transport that throws before it returns a promise", async () => {
    // A host that drops its adapter with an edit queued leaves the call
    // dereferencing a method that is gone. Thrown synchronously, it would
    // escape before `inFlight` is assigned and strand the batch.
    const write = vi.fn(() => {
      throw new TypeError("setPatientFields is not a function");
    }) as unknown as (f: Record<string, unknown>) => Promise<Record<string, WriteOutcome>>;
    const errors: string[][] = [];
    const w = new PatientFieldWriter(write, { debounceMs: 5, onError: (f) => errors.push(f) });
    w.save("a", 1);
    await new Promise((r) => setTimeout(r, 20));
    await tick();
    expect(errors).toEqual([["a"]]);
    expect(w.outstanding).toEqual([]);
  });
});

describe("what is still owed", () => {
  it("names the fields with an edit that has not come back", async () => {
    const t = transport();
    const w = new PatientFieldWriter(t.write, { debounceMs: 5 });
    w.save("a", 1);
    expect(w.outstanding).toEqual(["a"]);
    await new Promise((r) => setTimeout(r, 20));
    // On the wire now, still outstanding.
    expect(w.outstanding).toEqual(["a"]);
    w.save("b", 2);
    expect(w.outstanding.sort()).toEqual(["a", "b"]);
    await t.answer({ a: saved(1) });
    await tick();
    expect(w.outstanding).toEqual(["b"]);
  });
});


describe("a batch the record refuses", () => {
  // THE RULE: a rejected batch must not cost the reader an edit the server
  // did not object to. DRF rejects the whole request, so nothing was
  // written — reporting all of it unsaved was true. What it cost was the
  // rest of the batch, with no way to tell which field poisoned it.

  it("sends the rest again when the server names the one it refused", async () => {
    const t = refusing([["flipi_score_options"], null]);
    const failed: string[][] = [];
    const w = new PatientFieldWriter(t.write, {
      debounceMs: 1,
      onError: (fields) => failed.push(fields),
    });

    w.save("flipi_score_options", "not a factor");
    w.save("hemoglobin_g_dl", 12);
    await new Promise((r) => setTimeout(r, 20));

    expect(failed).toEqual([["flipi_score_options"]]);
    expect(t.calls).toEqual([
      { flipi_score_options: "not a factor", hemoglobin_g_dl: 12 },
      { hemoglobin_g_dl: 12 },
    ]);
  });

  it("fails the whole batch when the server names nothing", async () => {
    // A 500, or a `{"detail": …}` body. Nothing to retry and nothing to
    // blame, so this is exactly the old behaviour — everything unsaved is
    // reported unsaved, and nothing is left in "Saving…" for ever.
    const t = refusing([[]]);
    const failed: string[][] = [];
    const w = new PatientFieldWriter(t.write, {
      debounceMs: 1,
      onError: (fields) => failed.push(fields),
    });

    w.save("hemoglobin_g_dl", 12);
    w.save("platelet_count", 200);
    await new Promise((r) => setTimeout(r, 20));

    expect(failed).toEqual([["hemoglobin_g_dl", "platelet_count"]]);
    expect(t.calls).toHaveLength(1);
  });

  it("fails the batch when the error names something that was not sent", async () => {
    // A host adapter is free to throw whatever it likes, and this duck-types
    // `fields` off it. An error naming a field outside the batch leaves the
    // whole batch to re-queue, and `finally` re-drains — so without the
    // intersection this sends the identical request for ever while the
    // fields it carries sit in "Saving…". Counted, not just observed: an
    // infinite loop shows up as a growing call log, not a failing assert.
    const t = refusing([["a_field_from_another_request"], null]);
    const failed: string[][] = [];
    const w = new PatientFieldWriter(t.write, {
      debounceMs: 1,
      onError: (fields) => failed.push(fields),
    });

    w.save("hemoglobin_g_dl", 12);
    await new Promise((r) => setTimeout(r, 40));

    expect(t.calls).toEqual([{ hemoglobin_g_dl: 12 }]);
    expect(failed).toEqual([["hemoglobin_g_dl"]]);
  });

  it("terminates when the retry is refused for a different field", async () => {
    // Each refusal strictly shrinks the batch, so this cannot loop.
    const t = refusing([["a"], ["b"], null]);
    const failed: string[][] = [];
    const w = new PatientFieldWriter(t.write, {
      debounceMs: 1,
      onError: (fields) => failed.push(fields),
    });

    w.save("a", 1);
    w.save("b", 2);
    w.save("c", 3);
    await new Promise((r) => setTimeout(r, 40));

    expect(failed).toEqual([["a"], ["b"]]);
    expect(t.calls).toEqual([{ a: 1, b: 2, c: 3 }, { b: 2, c: 3 }, { c: 3 }]);
  });

  it("does not undo an edit made while the refused batch was in flight", async () => {
    // The re-queued value is OLDER than anything that arrived since, and
    // putting it back must not overwrite the newer one.
    const calls: Array<Record<string, unknown>> = [];
    let refuse: (() => void) | null = null;
    const write = vi.fn(
      (fields: Record<string, unknown>) =>
        new Promise<Record<string, WriteOutcome>>((resolve, reject) => {
          calls.push({ ...fields });
          if (calls.length === 1) {
            refuse = () =>
              reject(Object.assign(new Error("refused"), { fields: ["bad"] }));
          } else {
            resolve({});
          }
        }),
    );
    const w = new PatientFieldWriter(write, { debounceMs: 1 });

    w.save("bad", "x");
    w.save("hemoglobin_g_dl", 12);
    await new Promise((r) => setTimeout(r, 10));
    w.save("hemoglobin_g_dl", 13); // while the first is on the wire
    refuse!();
    await new Promise((r) => setTimeout(r, 20));

    expect(calls[1]).toEqual({ hemoglobin_g_dl: 13 });
  });
});
