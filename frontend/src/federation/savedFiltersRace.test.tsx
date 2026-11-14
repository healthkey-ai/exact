/** The saved settings arriving while the reader is already typing.
 *
 *  `useSavedFilters` reports both halves of that race — the keys, always,
 *  and whether the values may be applied — and the panel has to honour the
 *  second half without dropping the first. The keys are what make a filter
 *  clearable later; the values are what the reader can see.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, renderTrialMatches } from "../test/renderTrialMatches";

/** A store whose read can be held open, and whose row a test can read back.
 *  `savePreferences` is handed the whole row, so it replaces. */
const heldStore = (row: Record<string, unknown>) => {
  let stored = { ...row };
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  return {
    release: () => release(),
    read: () => stored,
    // The row is read when the request LEAVES, not when it is released: a
    // real read in flight carries what the server held at the time it was
    // issued, which is the whole point of the race being tested.
    getPreferences: vi.fn(async () => {
      const inFlight = { ...stored };
      await held;
      return inFlight;
    }),
    savePreferences: vi.fn(async (filters: Record<string, unknown>) => {
      stored = { ...filters };
    }),
    resetPreferences: vi.fn(async () => {
      stored = {};
    }),
  };
};

/** A store that answers each read differently, and can hold the Nth open. */
const scriptedStore = (rows: Record<string, unknown>[], holdRead: number) => {
  let call = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  return {
    release: () => release(),
    getPreferences: vi.fn(async () => {
      const row = rows[Math.min(call, rows.length - 1)];
      call += 1;
      if (call === holdRead) await held;
      return { ...row };
    }),
    savePreferences: vi.fn(async () => undefined),
    resetPreferences: vi.fn(async () => undefined),
  };
};

/** The same, wearing the shape of a full state adapter — which is a store of
 *  a different KIND for the hook, and the one swap it re-reads for. */
const asAdapter = <T extends object>(store: T) => ({
  ...store,
  listFavoriteIds: vi.fn(async () => []),
  listRegisteredIds: vi.fn(async () => []),
  listAdvancedEnrollments: vi.fn(async () => ({})),
  setFavorite: vi.fn(async () => undefined),
  setRegistered: vi.fn(async () => undefined),
});

const openPanel = async () => {
  await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
};

const sponsorBox = async () => (await screen.findByLabelText("Sponsor")) as HTMLInputElement;

describe("a saved set that lands while the reader is typing", () => {
  it("does not take back the field they were typing in", async () => {
    const api = fakeApi();
    const store = heldStore({ sponsor: "Stored", phase: "PHASE2" });
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openPanel();
    const sponsor = await sponsorBox();
    await userEvent.type(sponsor, "Acme");
    store.release();

    // The row lands — its untouched half proves it did, and held back
    // wholesale it would not: the reader would lose the rest of their own
    // saved settings for the session, one silent change for another — and
    // the box the reader is in still says what they typed.
    await waitFor(() => expect(api.listRequests().at(-1)!.params.phase).toBe("PHASE2"));
    // Through `waitFor`, not read once: under a loaded suite the row can land
    // a tick after the request that proves it did, and a bare read here flaked
    // exactly that way in a full run.
    await waitFor(async () => expect((await sponsorBox()).value).toBe("Acme"));
    // And what the search is narrowed by, once the box's own 400ms settles.
    await waitFor(() => expect(api.listRequests().at(-1)!.params.sponsor).toBe("Acme"));
  });

  it("still claims the keys, so a later clear of a loaded filter sticks", async () => {
    // The half `applied` does not govern. Ownership is what turns an empty
    // box into a tombstone; without it the clear reads as "nothing to
    // clear", the row keeps the value, and it comes back on the next mount.
    const api = fakeApi();
    const store = heldStore({ sponsor: "Stored", phase: "PHASE2" });
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openPanel();
    const sponsor = await sponsorBox();
    await userEvent.type(sponsor, "Acme");
    store.release();
    await waitFor(() => expect(api.listRequests().at(-1)!.params.phase).toBe("PHASE2"));

    // Now clear the field the ROW owned and the reader never typed in.
    const phase = screen.getByLabelText("Phase (this or later)") as HTMLSelectElement;
    await userEvent.selectOptions(phase, "");

    await waitFor(() => expect(store.read().phase).toBeUndefined());
  });

  it("does not hand a stale row back to a panel that was just Reset", async () => {
    // Reset overrules everything, and it empties the ownership set on its
    // way out — so a read still in flight had nothing left to measure the
    // row against, and repopulated the panel the reader had just cleared.
    const api = fakeApi();
    const store = heldStore({ sponsor: "Stored", phase: "PHASE2" });
    const view = renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openPanel();
    // Reset is only offered once a filter is active, so there has to be one.
    await userEvent.type(await sponsorBox(), "Acme");
    await userEvent.click(await screen.findByRole("button", { name: /^Reset/ }));
    store.release();

    // Give the read every chance to land on top of the reset.
    await waitFor(() => expect(store.getPreferences).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect((await sponsorBox()).value).toBe("");
    expect(screen.queryByDisplayValue("Stored")).toBeNull();
    expect(api.listRequests().at(-1)!.params.phase).toBeUndefined();

    // A control, because everything asserted above is an ABSENCE: delete the
    // delivery in `useSavedFilters` and every line of it still passes. A
    // second store — of a different KIND, which is what the hook re-reads
    // for — has to arrive, so the test can only stay green while this build
    // still hands rows to the panel. It says nothing about the veto: that
    // read arrives with `applied` true, on a branch that never consults it.
    const later = heldStore({ sponsor: "Later" });
    later.release();
    view.setProps({ state: asAdapter(later) });
    await waitFor(async () => expect((await sponsorBox()).value).toBe("Later"));
  });

  it("measures the race, not who owns the field", async () => {
    // Ownership is cumulative and survives a store swap for the same
    // patient; "did they touch it while THIS read was in flight" does not.
    // Read the first for the second and a new store's saved values arrive
    // suppressed, as though the reader had just typed every one of them.
    const api = fakeApi();
    const first = heldStore({ sponsor: "First" });
    first.release();
    const view = renderTrialMatches(api, { preferences: first });
    await waitFor(() => expect(api.listRequests().at(-1)!.params.sponsor).toBe("First"));

    // A store of a different KIND for the same patient — the one case the
    // hook re-reads for, and the one where ownership carries over.
    const secondRow = heldStore({ sponsor: "Second", phase: "PHASE3" });
    const second = {
      ...secondRow,
      listFavoriteIds: vi.fn(async () => []),
      listRegisteredIds: vi.fn(async () => []),
      listAdvancedEnrollments: vi.fn(async () => ({})),
      setFavorite: vi.fn(async () => undefined),
      setRegistered: vi.fn(async () => undefined),
    };
    view.setProps({ state: second });
    await openPanel();
    // An edit during the new read, in a field that is NOT the one at issue.
    await userEvent.type(await screen.findByLabelText("Title"), "dara");
    secondRow.release();

    // The new store's sponsor lands: the reader never touched that box
    // during this read, whoever owned it before.
    await waitFor(() => expect(api.listRequests().at(-1)!.params.phase).toBe("PHASE3"));
    await waitFor(async () => expect((await sponsorBox()).value).toBe("Second"));
  });

  it("keeps Reset standing through an edit made while the read is still out", async () => {
    // Reset overrules the WHOLE row, and it has to go on doing that for as
    // long as the read it raced is still coming. Between the two the reader
    // carries on filtering, and an edit is not a reason to start trusting a
    // row that describes the panel they cleared: lower the veto there and
    // the stale set repopulates the panel behind the field they are typing
    // in. The tests above stop at the Reset, so nothing held this.
    const api = fakeApi();
    const store = heldStore({ sponsor: "Stored", phase: "PHASE2" });
    const view = renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openPanel();
    await userEvent.type(await sponsorBox(), "Acme");
    await userEvent.click(await screen.findByRole("button", { name: /^Reset/ }));
    // The step that was missing: an edit AFTER the Reset, before the row lands.
    await userEvent.type(await screen.findByLabelText("Title"), "dara");
    store.release();

    await waitFor(() => expect(store.getPreferences).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect((await sponsorBox()).value).toBe("");
    expect(screen.queryByDisplayValue("Stored")).toBeNull();
    expect(api.listRequests().at(-1)!.params.phase).toBeUndefined();
    // What they typed after the Reset is theirs, and is still there.
    expect((await screen.findByLabelText("Title")) as HTMLInputElement).toHaveValue("dara");

    // The same delivery control as the Reset tests below: without it this
    // test passes against a build where no row ever reaches the panel.
    const later = heldStore({ sponsor: "Later" });
    later.release();
    view.setProps({ state: asAdapter(later) });
    await waitFor(async () => expect((await sponsorBox()).value).toBe("Later"));
  });

  it("does not hand the reader ownership of a row Reset threw away", async () => {
    // The keys are claimed so that a later clear can be a tombstone — but a
    // row that lost a race with Reset describes a panel that no longer
    // exists, and the transport has already discarded it. Claimed anyway,
    // the host's baseline gets written back later as the reader's own
    // standing preference.
    const api = fakeApi();
    const store = heldStore({ phase: "PHASE2" });
    const view = renderTrialMatches(api, {
      preferences: store,
      initialFilters: { phase: "PHASE1" },
    });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openPanel();
    await userEvent.type(await sponsorBox(), "Acme");
    await userEvent.click(await screen.findByRole("button", { name: /^Reset/ }));
    store.release();
    await waitFor(() => expect(store.getPreferences).toHaveBeenCalled());

    // An unrelated edit, which is what carries the owned set to the server.
    await userEvent.type(await screen.findByLabelText("Title"), "dara");

    await waitFor(() => expect(store.read().searchTitle).toBe("dara"));
    // The host's mount-time scope is not the reader's preference.
    expect(store.read().phase).toBeUndefined();

    // A control, because everything asserted above is an ABSENCE: delete the
    // delivery in `useSavedFilters` and every line of it still passes. A
    // second store — of a different KIND, which is what the hook re-reads
    // for — has to arrive, so the test can only stay green while this build
    // still hands rows to the panel. It says nothing about the veto: that
    // read arrives with `applied` true, on a branch that never consults it.
    const later = heldStore({ sponsor: "Later" });
    later.release();
    view.setProps({ state: asAdapter(later) });
    await waitFor(async () => expect((await sponsorBox()).value).toBe("Later"));
  });


  it("does not read the trial-type MASK as something the reader typed", async () => {
    // The panel is handed `effectiveFilters`, where a trial type belonging to
    // the previous patient is masked. Compared against the raw state behind
    // it, that mask reads as an edit — so an unrelated edit during the new
    // patient's read vetoes the type THEY had saved, and the claim that
    // follows deletes it from their row on the next write.
    const api = fakeApi();
    const store = scriptedStore(
      [{ trialType: "drug" }, { trialType: "device", phase: "PHASE3" }],
      2,
    );
    // `patientInfo: null` on purpose: the harness supplies a default payload
    // otherwise, and an inline payload NAMES the patient — so `personId`
    // would be ignored, the identity would never change, and the mask this
    // test exists for would never come on.
    const view = renderTrialMatches(api, {
      preferences: store,
      personId: "p1",
      patientInfo: null,
    });
    await waitFor(() => expect(api.listRequests().at(-1)!.params.trialType).toBe("drug"));

    view.setProps({ preferences: store, personId: "p2", patientInfo: null });
    await openPanel();
    await userEvent.type(await screen.findByLabelText("Title"), "dara");
    store.release();

    // The new patient's own saved type arrives, alongside the rest of their row.
    await waitFor(() => expect(api.listRequests().at(-1)!.params.phase).toBe("PHASE3"));
    expect(api.listRequests().at(-1)!.params.trialType).toBe("device");
  });

  it("does not carry a veto across to the next read", async () => {
    // A read that answers with an empty row never calls back — and a read
    // that fails or is cancelled never calls back either. Whatever the reader
    // overruled while it was in flight must not still be standing when the
    // NEXT read lands, or the new store's values arrive suppressed.
    const api = fakeApi();
    const first = scriptedStore([{}], 0);
    const view = renderTrialMatches(api, { preferences: first });
    await waitFor(() => expect(first.getPreferences).toHaveBeenCalled());

    await openPanel();
    await userEvent.type(await sponsorBox(), "Acme");

    const second = scriptedStore([{ sponsor: "Second", phase: "PHASE3" }], 1);
    view.setProps({ state: asAdapter(second) });
    // An edit while the SECOND read is in flight, in another field entirely.
    await userEvent.type(await screen.findByLabelText("Title"), "dara");
    second.release();

    await waitFor(() => expect(api.listRequests().at(-1)!.params.phase).toBe("PHASE3"));
    expect((await sponsorBox()).value).toBe("Second");
  });

  it("applies the row as it stands when nobody was typing", async () => {
    const api = fakeApi();
    const store = heldStore({ sponsor: "Stored" });
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    store.release();

    await waitFor(() => expect(api.listRequests().at(-1)!.params.sponsor).toBe("Stored"));
  });
});
