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

  it("is not fooled by an inherited property name", async () => {
    // `"constructor" in {}` is true, so a membership test that walks the
    // prototype chain accepts it, leaves the whole batch to re-queue, and
    // `finally` re-drains — the identical request for ever. Unreachable
    // from a DRF field name; reachable from any host adapter, which is what
    // the intersection is there to be safe against.
    const t = refusing([["constructor"], null]);
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

describe("a write that outlived the reader who made it (#583)", () => {
  // EXACT keys the patient row on the bearer token, so a payload sent under
  // somebody else's credential is written into somebody else's record. The
  // queue picks its adapter at flush time and no React key can catch an
  // identity that changes without a session signal — #583 has the four
  // measured shapes — so the comparison is made here, on the credential.
  //
  // `identity` is a plain function in these tests because that is all the
  // writer asks for. What produces the value in the app is
  // `fingerprintOf(token)`, tested next door.
  const writerWith = (
    identity: () => string | undefined,
    write: (f: Record<string, unknown>) => Promise<Record<string, WriteOutcome>>,
    extra: Partial<{
      onError: (fields: string[], error: unknown) => void;
      onSettled: (field: string, outcome: WriteOutcome) => void;
      onBatchSettled: () => void;
    }> = {},
  ) => new PatientFieldWriter(write, { debounceMs: 0, identity, ...extra });

  it("does not send an edit made by somebody else", async () => {
    const write = vi.fn(
      async (_fields: Record<string, unknown>) => ({}) as Record<string, WriteOutcome>,
    );
    const onError = vi.fn();
    let who: string | undefined = "sub:iss|one";
    const w = writerWith(() => who, write, { onError });

    w.save("hemoglobin", 12);
    who = "sub:iss|two"; // the credential swaps during the debounce
    w.flush();
    await w.settled();

    expect(write).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toEqual(["hemoglobin"]);
    expect((onError.mock.calls[0][1] as { name: string }).name).toBe("IdentityChanged");
    expect((onError.mock.calls[0][1] as { fields: string[] }).fields).toEqual([
      "hemoglobin",
    ]);
  });

  it("strands only the fields that changed hands, and sends the rest", async () => {
    // One batch can straddle the switch: an edit made before it and one made
    // after. Keying the capture per BATCH would have to choose one answer for
    // both, and either choice is wrong for half of them.
    const write = vi.fn(
      async (_fields: Record<string, unknown>) => ({}) as Record<string, WriteOutcome>,
    );
    const onError = vi.fn();
    let who = "sub:iss|one";
    const w = writerWith(() => who, write, { onError });

    w.save("hemoglobin", 12);
    who = "sub:iss|two";
    w.save("platelets", 200);
    w.flush();
    await w.settled();

    expect(write).toHaveBeenCalledTimes(1);
    expect(write.mock.calls[0][0]).toEqual({ platelets: 200 });
    expect(onError.mock.calls[0][0]).toEqual(["hemoglobin"]);
  });

  it("does not re-read when the whole batch was stranded", async () => {
    // `onBatchSettled` is the re-read, and its premise is that a request
    // landed. Nothing left, so the match has not moved and re-reading would
    // be work done on behalf of a batch that never existed.
    const write = vi.fn(
      async (_fields: Record<string, unknown>) => ({}) as Record<string, WriteOutcome>,
    );
    const onBatchSettled = vi.fn();
    let who = "sub:iss|one";
    const w = writerWith(() => who, write, { onBatchSettled, onError: () => {} });

    w.save("hemoglobin", 12);
    who = "sub:iss|two";
    w.flush();
    await w.settled();

    expect(write).not.toHaveBeenCalled();
    expect(onBatchSettled).not.toHaveBeenCalled();
  });

  it("stops showing a stranded field as still saving", async () => {
    // `outstanding` is what the page paints its optimistic value from. A
    // field that will never be sent has to leave it, or the row shows a
    // value the record does not hold, for ever.
    const write = vi.fn(
      async (_fields: Record<string, unknown>) => ({}) as Record<string, WriteOutcome>,
    );
    let who = "sub:iss|one";
    const w = writerWith(() => who, write, { onError: () => {} });

    w.save("hemoglobin", 12);
    expect(w.outstanding).toEqual(["hemoglobin"]);
    who = "sub:iss|two";
    w.flush();
    await w.settled();

    expect(w.outstanding).toEqual([]);
  });

  it("lets a ROTATED credential through, which is the common case", async () => {
    // Firebase refreshes hourly. If this dropped the write, every reader who
    // edits across the hour loses it — a guard against a rare wrong write
    // costing a common right one.
    const write = vi.fn(
      async (_fields: Record<string, unknown>) => ({}) as Record<string, WriteOutcome>,
    );
    const onError = vi.fn();
    // The same fingerprint, which is what `fingerprintOf` gives two tokens
    // for one person; the tokens themselves differ and are not compared.
    const w = writerWith(() => "sub:iss|one", write, { onError });

    w.save("hemoglobin", 12);
    w.flush();
    await w.settled();

    expect(write).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it("does nothing at all when nobody told it who is signed in", async () => {
    // The default, and every existing caller. A queue with no way to learn
    // the identity cannot tell a stranger from a refresh, so it must not
    // pretend to: silence here means the guard is off, not that it passed.
    const write = vi.fn(
      async (_fields: Record<string, unknown>) => ({}) as Record<string, WriteOutcome>,
    );
    const w = new PatientFieldWriter(write, { debounceMs: 0 });

    w.save("hemoglobin", 12);
    w.flush();
    await w.settled();

    expect(write).toHaveBeenCalledWith({ hemoglobin: 12 });
  });

  it("guards the RETRY, which is the write a reader never sees", async () => {
    // The refusal path re-queues the fields the server did not name. Without
    // carrying their owner back with them they come back unowned, the guard
    // reads "unknown" and waves them through — so the one payload that
    // escapes is the one nobody is watching.
    const { write, calls } = refusing([["hemoglobin"], null]);
    const onError = vi.fn();
    let who = "sub:iss|one";
    const w = new PatientFieldWriter(write, {
      debounceMs: 0,
      identity: () => who,
      onError,
      onSettled: () => {},
    });

    w.save("hemoglobin", -1); // the value the vocabulary refuses
    w.save("platelets", 200); // innocent, and re-queued by the refusal
    w.flush();
    // The account changes while that batch is on the wire. Synchronously
    // after `flush`, because the retry is not a second `flush` — `finally`
    // re-drains as soon as the refusal settles, and that is the request
    // being guarded. A test that waited and then flushed would watch the
    // retry go out first and prove nothing.
    who = "sub:iss|two";
    await w.settled();
    await tick();

    // First attempt carried both; the server named only one.
    expect(calls[0]).toEqual({ hemoglobin: -1, platelets: 200 });
    expect(calls).toHaveLength(1); // and the retry never left
    const stranded = onError.mock.calls.map((c) => c[1] as { name?: string });
    expect(stranded.some((e) => e?.name === "IdentityChanged")).toBe(true);
    const names = onError.mock.calls.flatMap((c) => c[0] as string[]);
    expect(names).toContain("platelets");
  });

  it("keeps a superseded edit under whoever made it last", async () => {
    // A refusal re-queues, but a field the reader edited AGAIN mid-flight is
    // not re-queued — the newer value is already there, under the newer
    // owner. Carrying the old owner back over it would strand an edit the
    // current reader just made.
    const { write, calls } = refusing([["hemoglobin"], null]);
    const onError = vi.fn();
    const w = new PatientFieldWriter(write, {
      debounceMs: 0,
      identity: () => "sub:iss|one",
      onError,
      onSettled: () => {},
    });

    w.save("hemoglobin", -1);
    w.save("platelets", 200);
    w.flush();
    // Edited again while the first batch is on the wire.
    w.save("platelets", 300);
    await w.settled();
    await tick();
    await w.settled();

    expect(calls[1]).toEqual({ platelets: 300 });
    expect(
      onError.mock.calls.some(
        (c) => (c[1] as { name?: string })?.name === "IdentityChanged",
      ),
    ).toBe(false);
  });
});
