// The counter behind "Saving…".
//
// THE MODEL, written down because a fix that honoured it at one level broke
// it at the next: `owed[field]` is the number of saves for that field that
// have not been answered. Every `save()` adds one; every answer from the
// wire — settled, refused, or superseded — takes exactly one away. A field
// shows "Saving…" while the count is above zero, so a save that never
// produces an answer leaves the row saying it is still writing a value the
// record already holds, for the rest of the session.
//
// The retry in `PatientFieldWriter` broke that by MERGING a re-queued value
// with a newer one the reader had typed meanwhile: two saves, one answer,
// counter stuck at one. Hence the superseded report — the ended attempt is
// still an answer, and `onSettled` already knows to stand down when a newer
// edit is owed.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useQueuedPatientFields } from "./hooks";
import type { TrialStateAdapter, WriteOutcome } from "./state";

function harness(refuse: (fields: Record<string, unknown>) => string[] | null) {
  const calls: Array<Record<string, unknown>> = [];
  const setPatientFields = vi.fn(async (fields: Record<string, unknown>) => {
    calls.push({ ...fields });
    const named = refuse(fields);
    if (named) throw Object.assign(new Error("refused"), { fields: named });
    return Object.fromEntries(
      Object.entries(fields).map(([f, v]) => [f, { status: "saved", value: v } as WriteOutcome]),
    );
  });
  const state = { setPatientFields, getWritableFields: vi.fn(async () => ({})) };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(
    () => useQueuedPatientFields(state as unknown as TrialStateAdapter, "p1"),
    { wrapper },
  );
  return { view, calls };
}

const settle = () => new Promise((r) => setTimeout(r, 500));

describe("a field the reader edits again while its batch is refused", () => {
  it("does not stay on Saving… for ever", async () => {
    // a is refused; b is edited a second time WHILE that batch is on the
    // wire. The refusal is held open until then, because the interleaving
    // is the whole test: let it land first and b is simply re-queued, the
    // counter balances, and the bug is invisible.
    //
    // So the re-queued b:1 is stale, the queued b:2 is what should go, and
    // the attempt carrying b:1 still has to be answered or its count never
    // comes down.
    const calls: Array<Record<string, unknown>> = [];
    let refuse: (() => void) | null = null;
    const setPatientFields = vi.fn(
      (fields: Record<string, unknown>) =>
        new Promise<Record<string, WriteOutcome>>((resolve, reject) => {
          calls.push({ ...fields });
          if ("a" in fields) {
            refuse = () =>
              reject(Object.assign(new Error("refused"), { fields: ["a"] }));
            return;
          }
          resolve(
            Object.fromEntries(
              Object.entries(fields).map(([f, v]) => [
                f,
                { status: "saved", value: v } as WriteOutcome,
              ]),
            ),
          );
        }),
    );
    const state = { setPatientFields, getWritableFields: vi.fn(async () => ({})) };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const view = renderHook(
      () => useQueuedPatientFields(state as unknown as TrialStateAdapter, "p1"),
      { wrapper },
    );

    act(() => {
      view.result.current.save("a", "bad");
      view.result.current.save("b", 1);
    });
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => view.result.current.save("b", 2));
    await act(async () => {
      refuse!();
      await settle();
    });

    await waitFor(() => expect(view.result.current.outstanding).toEqual({}));
    expect(view.result.current.failed).toEqual({ a: "bad" });
    expect(calls).toEqual([{ a: "bad", b: 1 }, { b: 2 }]);
  });

  it("retires a re-queued field the reader did not touch again", async () => {
    // The ordinary case: nothing supersedes b, so the retry answers for it.
    const { view, calls } = harness((fields) => ("a" in fields ? ["a"] : null));

    act(() => {
      view.result.current.save("a", "bad");
      view.result.current.save("b", 1);
    });
    await settle();

    await waitFor(() => expect(view.result.current.outstanding).toEqual({}));
    expect(view.result.current.failed).toEqual({ a: "bad" });
    expect(calls).toEqual([{ a: "bad", b: 1 }, { b: 1 }]);
  });

  it("retires everything when the refusal names nothing", async () => {
    const { view, calls } = harness(() => []);

    act(() => {
      view.result.current.save("a", 1);
      view.result.current.save("b", 2);
    });
    await settle();

    await waitFor(() => expect(view.result.current.outstanding).toEqual({}));
    expect(Object.keys(view.result.current.failed).sort()).toEqual(["a", "b"]);
    expect(calls).toHaveLength(1);
  });
});
