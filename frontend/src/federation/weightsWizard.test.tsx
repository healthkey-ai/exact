// The weights wizard: when it is offered, what it writes, and when it is not.
//
// The arithmetic is CB's and is tested as arithmetic. The rest is about a
// modal that appears unasked over a clinical list, so most of these are about
// NOT appearing: to a reader who has answered, to a host that cannot remember
// the answer, and before the page knows which of those is true.
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeApi, fakeState, renderTrialMatches } from "../test/renderTrialMatches";
import { WIZARD_WRITE_GRACE_MS } from "./TrialMatches";
import { WeightsWizard } from "./WeightsWizard";
import { FACTORS, PLACES, weightsFor } from "./weightRanking";
import { PreconditionFailed, type Precondition } from "./state";
import type { FilterState } from "./types";

const OFFER = /What matters most to you\?/;

/** A state adapter that can remember the offer. */
function withWizard(offered: boolean) {
  // `.adapter`, not the wrapper: `fakeState()` returns the adapter alongside
  // the arrays a test can assert on, and handing the wrapper to
  // `renderTrialMatches` gives the widget an object with no adapter methods
  // at all — which reads as "this host stores nothing" and quietly passes
  // every test that asserts an absence.
  const state = fakeState();
  const record = vi.fn().mockResolvedValue(undefined);
  return {
    state: {
      ...state.adapter,
      weightsWizard: { wasOffered: vi.fn().mockResolvedValue(offered), record },
    },
    record,
    savePreferences: state.adapter.savePreferences as ReturnType<typeof vi.fn>,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("the ranking, as arithmetic", () => {
  it("holds for every ranking a reader can give, not just one", () => {
    // 24 of them: four factors, three places. Exhaustive is cheaper here than
    // a property test and pins the same invariant — four distinct keys, the
    // four places, and a total of 100 whichever order they come in.
    const keys = FACTORS.map((f) => f.key);
    for (const a of keys)
      for (const b of keys.filter((k) => k !== a))
        for (const c of keys.filter((k) => k !== a && k !== b)) {
          const weights = weightsFor([a, b, c]) as Record<string, number>;
          expect(Object.keys(weights).sort()).toEqual([...keys].sort());
          expect([weights[a], weights[b], weights[c]]).toEqual([40, 30, 20]);
          expect(Object.values(weights).reduce((x, y) => x + y, 0)).toBe(100);
        }
  });

  it("still answers for every factor if a caller repeats one", () => {
    // Unreachable from the wizard, which narrows the list as it goes. But the
    // untouched version dropped a factor here: the repeat overwrote its own
    // first place and the unnamed factor fell off the end of `PLACES` to
    // `DEFAULT_WEIGHT`, which `weightsAreCustom` reads as "no opinion".
    const weights = weightsFor([
      "riskWeight",
      "riskWeight",
      "benefitWeight",
    ]) as Record<string, number>;
    expect(Object.keys(weights).sort()).toEqual(
      FACTORS.map((f) => f.key).sort(),
    );
    expect(weights.riskWeight).toBe(40);
    expect(weights.benefitWeight).toBe(30);
    expect(Object.values(weights).reduce((x, y) => x + y, 0)).toBe(100);
  });

  it("pays CB's places, in CB's order", () => {
    // The one thing the exhaustive test above cannot say, because it derives
    // its expectations from the same constant: these are the numbers CB uses.
    expect([...PLACES]).toEqual([40, 30, 20, 10]);
  });
});

describe("when it is offered", () => {
  it("appears for a reader who has never been asked", async () => {
    const { state } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    expect(await screen.findByText(OFFER)).toBeTruthy();
  });

  it("stays away from a reader who has", async () => {
    const { state } = withWizard(true);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText("Trial 1");
    expect(screen.queryByText(OFFER)).toBeNull();
  });

  it("stays away from a host that cannot remember the answer", async () => {
    // Asking without somewhere to record it means asking again every visit.
    renderTrialMatches(fakeApi(), { state: fakeState().adapter });
    await screen.findByText("Trial 1");
    expect(screen.queryByText(OFFER)).toBeNull();
  });

  it("waits for the saved filters before asking", async () => {
    // A modal over a list that is still settling asks a reader about
    // something they have not seen yet — and the answer would race the read
    // that says whether they have already been asked.
    //
    // "Waits" is bounded, and the bound is not here: `useSavedFilters` opens
    // its gate unconditionally after `SAVED_FILTERS_GRACE_MS`, so the real
    // behaviour is "waits up to 300ms, then asks anyway" and the list is
    // never hostage to the preferences service. What this pins is that the
    // gate is consulted at all.
    let release: (value: Record<string, never>) => void = () => {};
    const slow = new Promise<Record<string, never>>((resolve) => {
      release = resolve;
    });
    const base = fakeState().adapter;
    const state = {
      ...base,
      getPreferences: vi.fn(() => slow),
      weightsWizard: {
        wasOffered: vi.fn().mockResolvedValue(false),
        record: vi.fn().mockResolvedValue(undefined),
      },
    };

    renderTrialMatches(fakeApi(), { state });
    // Long enough for the offer to have appeared if nothing held it.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText(OFFER)).toBeNull();

    release({});
    expect(await screen.findByText(OFFER)).toBeTruthy();
  });

  it("stays away when the read fails", async () => {
    const state = {
      ...fakeState().adapter,
      weightsWizard: {
        wasOffered: vi.fn().mockRejectedValue(new Error("nope")),
        record: vi.fn(),
      },
    };
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText("Trial 1");
    expect(screen.queryByText(OFFER)).toBeNull();
  });
});

describe("answering it", () => {
  it("records a decline without asking anything", async () => {
    const { state, record } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));

    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(OFFER)).toBeNull();
  });

  it("walks three questions and narrows the list as it goes", async () => {
    const { state } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    expect(await screen.findByText(/most important factor when choosing/)).toBeTruthy();
    expect(screen.getAllByText(/^(Risk|Benefit|Patient Burden|Distance)$/)).toHaveLength(4);

    await userEvent.click(screen.getByRole("button", { name: /^Risk/ }));
    expect(await screen.findByText(/second most important/)).toBeTruthy();
    // The one already chosen is gone rather than offered and then refused.
    expect(screen.queryByRole("button", { name: /^Risk/ })).toBeNull();
    expect(screen.getAllByText(/^(Benefit|Patient Burden|Distance)$/)).toHaveLength(3);

    await userEvent.click(screen.getByRole("button", { name: /^Benefit/ }));
    expect(await screen.findByText(/third most important/)).toBeTruthy();
    expect(screen.getAllByText(/^(Patient Burden|Distance)$/)).toHaveLength(2);
  });

  it("saves the weights and records the offer once the third is picked", async () => {
    const { state, record, savePreferences } = withWizard(false);
    const api = fakeApi();
    renderTrialMatches(api, { state });
    await screen.findByText(OFFER);

    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^Distance/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Risk/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Benefit/ }));

    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    // The store debounces, so this one waits longer than the default: the
    // weights reach `filters` immediately and the wire a moment later.
    await waitFor(
      () =>
        expect(savePreferences).toHaveBeenCalledWith(
          expect.objectContaining({
            distancePenaltyWeight: 40,
            riskWeight: 30,
            benefitWeight: 20,
            patientBurdenWeight: 10,
          }),
        ),
      { timeout: 3000 },
    );
    expect(screen.queryByText(OFFER)).toBeNull();
  });

  it("re-sorts the list with the weights it just wrote", async () => {
    // The point of the wizard. CB's version stores the weights and leaves the
    // ranked list as it was until something else refetches it.
    const { state } = withWizard(false);
    const api = fakeApi();
    renderTrialMatches(api, { state });
    await screen.findByText(OFFER);

    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^Distance/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Risk/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Benefit/ }));

    await waitFor(() => {
      const last = api.listRequests().at(-1);
      expect(last?.params.distancePenaltyWeight).toBe("40");
    });
  });

  it("lets a reader back out of the questions to the offer", async () => {
    // Back from the first question is "I have changed my mind about
    // answering, show me the two choices again". Escape cannot say that: it
    // leaves the dialog entirely. Since #596 it no longer RECORDS anything
    // either, so the two differ in where they land, not in what they cost.
    const { state, record } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await screen.findByText(/most important factor when choosing/);
    await userEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByText(OFFER)).toBeTruthy();
    expect(record).not.toHaveBeenCalled();
  });

  it("drops later answers when an earlier one is changed", async () => {
    // Otherwise going back and picking differently leaves a ranking that
    // names the same factor twice.
    const { state } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^Risk/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Benefit/ }));
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    await userEvent.click(screen.getByRole("button", { name: "Back" }));

    // First answer changed: the second must be on offer again.
    await userEvent.click(await screen.findByRole("button", { name: /^Benefit/ }));
    expect(await screen.findByText(/second most important/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Risk/ })).toBeTruthy();
  });
});

describe("the ways out of it", () => {
  it("writes nothing when the reader clicks outside the panel (#596)", async () => {
    // The bug, end to end, at the level where it cost something. Measured on
    // the local stand 2026-09-28: a reader clicked the scrim, chose nothing,
    // reloaded, and the wizard was gone for good — PROMOP's row came back
    // `weights_wizard_offered: true` with `preferences: {}`, a decision
    // recorded for somebody who had not made one.
    const { state, record, savePreferences } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    const scrim = screen.getByRole("dialog").parentElement;
    expect(scrim).toHaveClass("exact-subform__scrim");
    await userEvent.click(scrim as HTMLElement);

    // Off the screen, and nothing said about it to anybody.
    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());
    expect(record).not.toHaveBeenCalled();
    // Not the weights either: a dismissal is not "keep them equal" being
    // written down, it is nothing being written down.
    expect(savePreferences).not.toHaveBeenCalled();
  });

  it("offers it again on the next visit, because nothing was recorded", async () => {
    // The consequence of writing nothing, from the reader's side: a fresh
    // mount asks again, because the only thing that remembers across visits
    // is the flag and a dismissal did not write it.
    //
    // `wasOffered` READS what `record` wrote rather than being pinned to
    // false. Pinned, the second mount re-offers whether or not the flag was
    // written, so the test passed with the whole fix reverted and only its
    // `record` assertion — a copy of the previous test's — did any work.
    // Wired to the same fake row, reverting the fix makes this fail on the
    // offer being ABSENT, which is the claim in the name.
    const state = fakeState();
    const record = vi.fn().mockResolvedValue(undefined);
    const store = {
      ...state.adapter,
      weightsWizard: {
        wasOffered: vi.fn(async () => record.mock.calls.length > 0),
        record,
      },
    };

    renderTrialMatches(fakeApi(), { state: store, personId: 11 });
    await screen.findByText(OFFER);
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());
    expect(record).not.toHaveBeenCalled();

    // The next visit: a new mount over the same row. A new mount rather than
    // a prop change, because a dismissal lasts exactly as long as
    // `readerHandle` does — and the test below is a prop change that MOVES
    // the handle and is asked again, which is the same rule, not a different
    // one.
    cleanup();
    renderTrialMatches(fakeApi(), { state: store, personId: 11 });
    expect(await screen.findByText(OFFER)).toBeTruthy();

    // And an ANSWER on that second visit does end it, so the re-offer above
    // is the flag speaking and not the mount.
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    cleanup();
    renderTrialMatches(fakeApi(), { state: store, personId: 11 });
    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());
  });

  /** Wait for the re-read the handle change triggers, then let it dispatch.
   *
   *  `waitFor(() => expect(queryByText(OFFER)).toBeNull())` cannot do this
   *  job: the dialog is already absent the instant the handle changes, so it
   *  passes on the first check and never sees the read land a tick later.
   *  Written that way the test below passed with a dismissal writing the
   *  flag again — measured. The claim has to be checked AFTER the read that
   *  decides whether the question comes back. */
  const afterTheReread = async (
    wasOffered: ReturnType<typeof vi.fn>,
    before: number,
  ) => {
    await waitFor(() => expect(wasOffered.mock.calls.length).toBeGreaterThan(before));
    await act(async () => {});
  };

  it("is not even re-read when a NAMED patient's profile is refreshed", async () => {
    // Where the "the modal comes back after an inline edit" worry lands once
    // the payload NAMES the patient: nowhere. `patientHandleOf` keys on the
    // id and ignores the rest of the payload precisely so a host re-reading
    // the profile (#555, which `setPatientFields` and `onPatientRecordChanged`
    // exist to cause) is not read as a change of patient. The handle does not
    // move, the reducer does not reset, and no second read is issued — so the
    // dismissal simply stands, with nothing added here to make it.
    //
    // Measured rather than assumed: a draft of this change carried a
    // dismissal memo justified by this case, and the case does not exist.
    const { state, record } = withWizard(false);
    const wasOffered = state.weightsWizard.wasOffered as ReturnType<typeof vi.fn>;
    const { setProps } = renderTrialMatches(fakeApi(), {
      state,
      personId: 11,
      patientInfo: { person_id: 9009, disease: "multiple myeloma", weight: 70 },
    });
    await screen.findByText(OFFER);

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());

    const reads = wasOffered.mock.calls.length;
    setProps({
      patientInfo: { person_id: 9009, disease: "multiple myeloma", weight: 71 },
    });
    await act(async () => {});
    expect(screen.queryByText(OFFER)).toBeNull();
    expect(wasOffered).toHaveBeenCalledTimes(reads);
    expect(record).not.toHaveBeenCalled();
  });

  it("asks again on every refresh when the payload names nobody", async () => {
    // The cost of #596, pinned so it is a decision and not a surprise. With
    // no id in the payload `patientHandleOf` hashes the whole payload, so a
    // refresh reads as a new patient, the reducer resets and the question
    // comes back — once per refresh, where before #596 it came once and the
    // offer was spent. Nothing here can close that: a dismissal is not
    // written down, and remembering one for a patient who cannot be NAMED
    // would mean remembering it for whoever came next. Repeated is the safe
    // direction; `TrialMatches` already warns such a host about this whole
    // class of problem, and #600 tracks it.
    const { state, record } = withWizard(false);
    const wasOffered = state.weightsWizard.wasOffered as ReturnType<typeof vi.fn>;
    const { setProps } = renderTrialMatches(fakeApi(), {
      state,
      personId: 11,
      patientInfo: { disease: "multiple myeloma", weight: 70 },
    });
    await screen.findByText(OFFER);
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());

    const before = wasOffered.mock.calls.length;
    setProps({ patientInfo: { disease: "multiple myeloma", weight: 71 } });
    await afterTheReread(wasOffered, before);
    expect(screen.queryByText(OFFER)).toBeTruthy();
    // And still nothing was recorded, so the offer is not being spent either.
    expect(record).not.toHaveBeenCalled();
  });

  it("does not seal one patient's dialog on another patient's write", async () => {
    // `dismissWizard` does not read `answering` at all, and this is why.
    // That ref is one slot cleared only when a write settles or its
    // eight-second grace expires, so testing it with `!== null` — which the
    // answer handlers still do, see #599 — seals every dialog, including one
    // belonging to a patient the reader has already moved to. A dismissal
    // must always take the question off the screen; that is the whole of
    // #596, and it cannot be conditional on somebody else's write.
    const { state, record } = withWizard(false);
    record.mockImplementation(() => new Promise<void>(() => {}));
    const { setProps } = renderTrialMatches(fakeApi(), { state, personId: 11 });
    await screen.findByText(OFFER);

    // Patient 11 answers, and the write hangs.
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));

    setProps({ personId: 22 });
    await screen.findByText(OFFER);
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());
    // And still nobody wrote anything for 22.
    expect(record).toHaveBeenCalledTimes(1);
  });

  it("does not let Escape close over an answer still on the wire", async () => {
    // `Dialog` dismisses on Escape, on its own Close button and on the scrim,
    // and `busy` reaches none of them — it disables the wizard's buttons and
    // nothing else. So Escape after the third question recorded a DECLINE
    // over a ranking still in flight, by the one path that skips the check
    // that the ranking saved.
    //
    // #596 took the decline out of Escape, which removes that particular
    // wrong write but NOT the reason for the seal: the dialog says "Saving
    // your answer…" and must not vanish while that is true, and a dismissal
    // must not close a state the settled write is about to close itself.
    // What is pinned here is that the ranking's own flag still lands exactly
    // once, after the ranking, with two dismissals in between.
    const { state, record, savePreferences } = withWizard(false);
    let release: () => void = () => {};
    savePreferences.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          release = () => resolve(undefined);
        }),
    );
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^Risk/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Benefit/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Distance/ }));
    await waitFor(() => expect(savePreferences).toHaveBeenCalled());

    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(record).not.toHaveBeenCalled();

    // And once the ranking is safe, the flag follows — exactly once.
    release();
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
  });

  it("puts focus on each question as it arrives", async () => {
    // `Dialog` moves focus in once, onto Close, and its effect never runs
    // again. Choosing a factor unmounts the focused button, so focus fell to
    // `document.body`: nothing was announced, and Tab put a screen-reader
    // user back on Close — which was the permanent decline when this was
    // written, and since #596 is merely the way out without answering. The
    // reason to move focus is unchanged: the first tab stop should be an
    // answer, not an exit.
    const { state } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    const offer = await screen.findByText(OFFER);
    await waitFor(() => expect(document.activeElement).toBe(offer));

    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    const first = await screen.findByText(/most important factor when choosing/);
    await waitFor(() => expect(document.activeElement).toBe(first));

    await userEvent.click(screen.getByRole("button", { name: /^Risk/ }));
    const second = await screen.findByText(/second most important/);
    await waitFor(() => expect(document.activeElement).toBe(second));
  });
});

describe("when the answer cannot be stored", () => {
  it("does not record the offer if the ranking failed to save", async () => {
    // The two writes reach the same row by different routes, and only the
    // weights' route can be refused. Recording anyway would mark the reader
    // as answered with their answer nowhere, and there is no second offer.
    const { state, record, savePreferences } = withWizard(false);
    savePreferences.mockRejectedValue(new Error("refused"));
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^Risk/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Benefit/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Distance/ }));

    // The dialog closes only once the write has settled, so its absence is
    // proof the decision about `record` has already been taken.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull(), {
      timeout: 3000,
    });
    expect(savePreferences).toHaveBeenCalled();
    expect(record).not.toHaveBeenCalled();
  });

  it("does not re-ask while the answer is still on the wire", async () => {
    // A host writing `state={createPromopState(...)}` inline hands over a new
    // `weightsWizard` on every render. Reading the flag again at that moment
    // reads it before the answer has written it.
    //
    // The guarantee itself lives in `weightsWizardState.ts` and is pinned
    // there, over generated sequences; this is the end-to-end witness that
    // the widget actually asks the model. It catches nothing the unit tests
    // do not, and is kept because the scenario — a real host pattern, named
    // in four places in `hooks.ts` — is worth having written down where
    // somebody reading the widget will find it.
    const base = fakeState().adapter;
    let release: () => void = () => {};
    const record = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = () => resolve();
        }),
    );
    const { setProps } = renderTrialMatches(fakeApi(), {
      state: {
        ...base,
        weightsWizard: { wasOffered: vi.fn().mockResolvedValue(false), record },
      },
    });
    await screen.findByText(OFFER);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));

    const second = {
      wasOffered: vi.fn().mockResolvedValue(false),
      record: vi.fn().mockResolvedValue(undefined),
    };
    setProps({ state: { ...base, weightsWizard: second } });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(second.wasOffered).not.toHaveBeenCalled();

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps the answer when the host re-reads the same patient's profile", async () => {
    // #555 has the host refresh `patientInfo` after an inline edit: the same
    // person, in a new object. `stateKey` hashes the whole payload and so
    // moves; the answer must not. Keyed on that, a re-read landing before the
    // flag reached the server put the question back up to a reader who had
    // just answered it.
    const { state, record } = withWizard(false);
    const asked = state.weightsWizard.wasOffered as ReturnType<typeof vi.fn>;
    const { setProps } = renderTrialMatches(fakeApi(), {
      state,
      personId: 11,
      patientInfo: { personId: 11, disease: "multiple myeloma" },
    });
    await screen.findByText(OFFER);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // Same patient, richer payload — what a profile re-read hands over.
    setProps({
      patientInfo: {
        personId: 11,
        disease: "multiple myeloma",
        country: "United States",
      },
    });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText(OFFER)).toBeNull();
    expect(asked).toHaveBeenCalledTimes(1);
  });

  it("offers it to the next patient, who has never been asked", async () => {
    // The other half of the switch, and the one a key check can silently
    // eat: A has answered, B has not. B's read must be allowed to put the
    // question up — and only one read is ever issued for B, so a result
    // dropped on the floor is dropped for good.
    const { state } = withWizard(true);
    const asked = state.weightsWizard.wasOffered as ReturnType<typeof vi.fn>;
    const { setProps } = renderTrialMatches(fakeApi(), { state, personId: 11 });
    await screen.findByText("Trial 1");
    expect(screen.queryByText(OFFER)).toBeNull();

    asked.mockResolvedValue(false);
    setProps({ personId: 22 });

    expect(await screen.findByText(OFFER)).toBeTruthy();
  });

  it("offers it to the next ACCOUNT, looking at the same patient", async () => {
    // The flag moved. It used to live in a row keyed on `person_id`, so
    // "same patient" was a fair answer to "same reader"; EXACT keys its own
    // row on the identity in the token, and a host that switches account
    // while showing the same patient changes neither `personId` nor the
    // payload. Keyed on the patient alone the previous account's `hide`
    // carried over, `shouldRead` refused a second read, and the new reader
    // was never asked at all.
    const { state } = withWizard(true);
    const asked = state.weightsWizard.wasOffered as ReturnType<typeof vi.fn>;
    const { setProps } = renderTrialMatches(fakeApi(), {
      state,
      personId: 11,
      stateIdentity: "k:user-1",
    });
    await screen.findByText("Trial 1");
    expect(screen.queryByText(OFFER)).toBeNull();

    asked.mockResolvedValue(false);
    setProps({ stateIdentity: "k:user-2" });

    expect(await screen.findByText(OFFER)).toBeTruthy();
  });

  it("does not carry one account's open wizard over to the next", async () => {
    // The other direction, and the worse one: the previous reader's
    // half-filled form still on screen in front of somebody else.
    const { state } = withWizard(false);
    const { setProps } = renderTrialMatches(fakeApi(), {
      state,
      personId: 11,
      stateIdentity: "k:user-1",
    });
    await screen.findByText(OFFER);

    (state.weightsWizard.wasOffered as ReturnType<typeof vi.fn>).mockResolvedValue(
      true,
    );
    setProps({ stateIdentity: "k:user-2" });

    expect(screen.queryByText(OFFER)).toBeNull();
  });

  it("does not carry one patient's offer over to the next", async () => {
    // The host can switch patients without unmounting. Until the new
    // reader's flag has been read, there is no question to put to them —
    // and the one on screen was asked about somebody else.
    const { state } = withWizard(false);
    const { setProps } = renderTrialMatches(fakeApi(), { state, personId: 11 });
    await screen.findByText(OFFER);

    (state.weightsWizard.wasOffered as ReturnType<typeof vi.fn>).mockResolvedValue(
      true,
    );
    setProps({ personId: 22 });

    // Immediately, not eventually. `waitFor` here would pass on the mocked
    // read resolving a tick later and would say nothing about the interval
    // this exists for — the one where the previous reader's question is
    // still on screen in front of somebody else.
    expect(screen.queryByText(OFFER)).toBeNull();
  });
});

describe("what the dialog says while it is writing", () => {
  it("names the wait on the offer, and marks itself busy", async () => {
    // Every control is disabled during the write, which leaves `Dialog`'s
    // focus trap cycling on one button whose handler is a no-op. Without
    // this that is indistinguishable from a hung dialog.
    render(<WeightsWizard busy onDecline={vi.fn()} onDismiss={vi.fn()} onFinish={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent("Saving your answer…");
    expect(
      screen.getByRole("dialog").querySelector(".exact-wizard"),
    ).toHaveAttribute("aria-busy", "true");
  });

  it("names it on the questions too, which is the screen that waits", async () => {
    // The first version put the word on the offer's primary button. That is
    // the FAST path — one unconditional PATCH. Finishing the three questions
    // is the slow one: a 400ms debounce flush, a write, and possibly a
    // refusal, a re-read and a retry. That screen has no primary button to
    // relabel, so it said nothing at all.
    const { rerender } = render(
      <WeightsWizard onDecline={vi.fn()} onDismiss={vi.fn()} onFinish={vi.fn()} />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await screen.findByText(/most important factor when choosing/);
    expect(screen.queryByRole("status")).toBeNull();

    rerender(<WeightsWizard busy onDecline={vi.fn()} onDismiss={vi.fn()} onFinish={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Saving your answer…");
  });

  it("refuses Escape and Close on its own, without help from the caller", async () => {
    // Two guards stand between a dismissal and a closed dialog mid-write:
    // this one, and `TrialMatches`'s refusal to act while saving. Each covers
    // the other, so neither shows up when the other is removed — which is
    // why this one is pinned here, away from the caller.
    const onDismiss = vi.fn();
    render(
      <WeightsWizard busy onDecline={vi.fn()} onDismiss={onDismiss} onFinish={vi.fn()} />,
    );

    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onDismiss).not.toHaveBeenCalled();

    // And the guard is about `busy`, not about the dialog: not busy, it goes.
    cleanup();
    const second = vi.fn();
    render(
      <WeightsWizard onDecline={vi.fn()} onDismiss={second} onFinish={vi.fn()} />,
    );
    await userEvent.keyboard("{Escape}");
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe("which gestures answer, and which merely close (#596)", () => {
  // Rule 6 at the only level that can see a gesture at all. The reducer
  // property says `saving` is reachable by an answer and nothing else; these
  // say which finger movements produce one, which is the half that was
  // wrong: every exit went to `onDecline`, so a click landing outside the
  // panel was recorded as a decision the reader never made.
  const spies = () => ({ onDecline: vi.fn(), onDismiss: vi.fn(), onFinish: vi.fn() });

  const gestures: Array<[string, () => Promise<unknown>]> = [
    ["a click on the scrim", async () => {
      // The scrim is the panel's parent and carries no role, so there is
      // nothing to query it by. Clicking the dialog's own container would
      // hit the panel's `stopPropagation` and prove the opposite of what
      // this is for, so the element is taken structurally and asserted to be
      // the scrim before it is clicked.
      const scrim = screen.getByRole("dialog").parentElement;
      expect(scrim).toHaveClass("exact-subform__scrim");
      return userEvent.click(scrim as HTMLElement);
    }],
    ["the Close button", () =>
      userEvent.click(screen.getByRole("button", { name: "Close" }))],
    ["the Escape key", () => userEvent.keyboard("{Escape}")],
  ];

  for (const [name, gesture] of gestures) {
    it(`does not spend the offer on ${name}`, async () => {
      const on = spies();
      render(<WeightsWizard {...on} />);
      await gesture();
      expect(on.onDismiss).toHaveBeenCalledTimes(1);
      expect(on.onDecline).not.toHaveBeenCalled();
      expect(on.onFinish).not.toHaveBeenCalled();
    });
  }

  it("spends it on Keep them equal, which is the answer with no questions", async () => {
    const on = spies();
    render(<WeightsWizard {...on} />);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    expect(on.onDecline).toHaveBeenCalledTimes(1);
    expect(on.onDismiss).not.toHaveBeenCalled();
  });

  it("does not close when a click lands inside the panel", async () => {
    // The other half of the scrim case, and the reason the scrim test above
    // has to reach for the parent: a click on the panel must not bubble out
    // to the scrim's handler, or reading the dialog would dismiss it.
    const on = spies();
    render(<WeightsWizard {...on} />);
    await userEvent.click(screen.getByText(/They currently count equally/));
    expect(on.onDismiss).not.toHaveBeenCalled();
    expect(on.onDecline).not.toHaveBeenCalled();
  });

  it("keeps the line on the questions screen too", async () => {
    // The dialog is rendered twice, from two `<Dialog>` call sites, and only
    // the first one carries the two answer buttons. A fix applied to the
    // offer alone would leave a reader who opened the questions and then
    // clicked away still spending the offer.
    const on = spies();
    render(<WeightsWizard {...on} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await screen.findByText(/most important factor when choosing/);

    await userEvent.keyboard("{Escape}");
    expect(on.onDismiss).toHaveBeenCalledTimes(1);
    expect(on.onDecline).not.toHaveBeenCalled();
    expect(on.onFinish).not.toHaveBeenCalled();
  });
});

describe("what it asks the server, and how often", () => {
  it("reads the flag once per patient, however often the host re-renders", async () => {
    // A host writing `state={createPromopState(...)}` inline hands over a new
    // `weightsWizard` on every render. Keyed on that alone, each render
    // cancelled the read IN FLIGHT and issued another — so a host that
    // re-renders continuously never got an answer and never showed the
    // wizard, while issuing one GET per render.
    //
    // The re-renders have to land inside that window to mean anything: while
    // the saved filters are still pending the effect has not started, and
    // after the answer arrives the state guard already covers it.
    let answer: (offered: boolean) => void = () => {};
    const wasOffered = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          answer = resolve;
        }),
    );
    const base = fakeState().adapter;
    const record = vi.fn().mockResolvedValue(undefined);
    const withStore = () => ({ ...base, weightsWizard: { wasOffered, record } });

    const { setProps } = renderTrialMatches(fakeApi(), { state: withStore() });
    // The read has started and has not answered.
    await waitFor(() => expect(wasOffered).toHaveBeenCalledTimes(1));

    setProps({ state: withStore() });
    setProps({ state: withStore() });
    setProps({ state: withStore() });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wasOffered).toHaveBeenCalledTimes(1);

    // And the one read still decides.
    answer(false);
    expect(await screen.findByText(OFFER)).toBeTruthy();
  });

  it("does not leave the filter writer quoting a tag the flag has moved", async () => {
    // `record()` writes the same ROW by another route, so it moves
    // `updated_at` — the tag the writer sends as `If-Match`. Unless the
    // writer is told, the first filter save after the wizard is refused and
    // costs a re-read and a retry, and in a second tab that retry can drop
    // the edit.
    let version = 0;
    const row = { tag: '"v0"', filters: {} as FilterState };
    const write = vi.fn(async (filters: FilterState, precondition: Precondition) => {
      if (precondition.kind === "ifMatch" && precondition.version !== row.tag) {
        throw new PreconditionFailed(row.tag);
      }
      row.filters = { ...filters };
      row.tag = `"v${(version += 1)}"`;
      return row.tag;
    });
    const state = {
      ...fakeState().adapter,
      preferenceVersioning: {
        read: async () => ({ filters: { ...row.filters }, version: row.tag }),
        write,
        clear: async () => row.tag,
      },
      weightsWizard: {
        wasOffered: vi.fn().mockResolvedValue(false),
        // What the server does: a different endpoint, the same row, a new tag.
        record: vi.fn(async () => {
          row.tag = '"afterTheFlag"';
        }),
      },
    };

    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);
    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^Risk/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Benefit/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Distance/ }));
    await waitFor(() => expect(write).toHaveBeenCalledTimes(1), { timeout: 3000 });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // The next filter save. One attempt, not a refusal and a retry.
    await userEvent.click(
      screen.getByRole("button", { name: /Suitability Preferences/ }),
    );
    const risk = await screen.findByLabelText(/Risk/);
    await userEvent.clear(risk);
    await userEvent.type(risk, "35");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(write).toHaveBeenCalledTimes(2), { timeout: 3000 });
    expect(write.mock.calls[1][1]).toEqual({
      kind: "ifMatch",
      version: '"afterTheFlag"',
    });
  });
});

describe("answering twice in one breath", () => {
  it("records the decline once, however many clicks arrive together", async () => {
    // The reducer refuses the second `answer`, but the WRITE belongs to the
    // widget and it cannot see that refusal: `wizardOpen` and `wizardBusy`
    // are render-scoped, so two answers dispatched in the same task both read
    // "open, not busy". Not reachable through the UI today; it is the next
    // control added to the panel that would find it.
    //
    // Aimed at "Keep them equal" rather than at Escape, which is what it used
    // to press. Since #596 Escape writes nothing at all, so pressing it three
    // times says nothing about a doubled WRITE — the test would have passed
    // with the caller-side guard deleted. The gesture has to be one that
    // spends the offer, and this is the only one that does it in a single
    // click.
    const { state, record } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);
    const skip = screen.getByRole("button", { name: "Keep them equal" });

    // Three inside ONE `act`, dispatched straight at the button. Each
    // `fireEvent`/`userEvent` wraps itself in its own `act`, so a render
    // lands between them and the second one already sees `busy` — which is
    // the very render-scoped reading this is about, and would make the test
    // agree with the bug.
    await act(async () => {
      for (let i = 0; i < 3; i += 1) {
        skip.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      }
    });

    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(record).toHaveBeenCalledTimes(1);
  });

  it("closes once, and writes nothing, however many dismissals arrive together", async () => {
    // The same race on the path that does NOT write. Three Escapes in one
    // task all read "open, not busy" and all dispatch; the reducer makes the
    // second and third no-ops, and none of them may reach `record`.
    const { state, record } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    await act(async () => {
      for (let i = 0; i < 3; i += 1) {
        document.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
        );
      }
    });

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(record).not.toHaveBeenCalled();
  });
});

describe("when the write never comes back", () => {
  it("lets the reader go rather than sealing the page", async () => {
    // While the answer is on the wire the dialog is sealed on purpose:
    // Escape, Close and the scrim all refuse, so a dismissal cannot record a
    // decline over a ranking in flight. Nothing in this package times a
    // request out, so those two together left a modal the reader never opened
    // sitting over a clinical trial list with no way out but a reload.
    //
    // `fireEvent`, not `userEvent`: the latter schedules its own timers, and
    // pairing that with fake ones hung the test and then leaked the fake
    // clock into the next one. One synchronous click is all this needs.
    const { state } = withWizard(false);
    // A request that is never answered and never rejected.
    (state.weightsWizard.record as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise<void>(() => {}),
    );
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
      await act(async () => {
        await Promise.resolve();
      });
      // Sealed, and saying so. Scoped to the dialog: the list has a status
      // region of its own, and an unscoped query matches whichever comes
      // first in the DOM.
      expect(
        within(screen.getByRole("dialog")).getByRole("status"),
      ).toHaveTextContent("Saving your answer…");

      await act(async () => {
        vi.advanceTimersByTime(WIZARD_WRITE_GRACE_MS);
      });
      expect(screen.queryByRole("dialog")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("where focus goes afterwards", () => {
  it("hands focus to the list, not to nowhere", async () => {
    // `Dialog` returns focus to whatever had it when the dialog mounted,
    // which for a modal nobody opened is `document.body`. Answering therefore
    // left focus nowhere and the next Tab started at the top of the host's
    // page, several screens above the list the reader was reading.
    const { state, record } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("heading", { name: "Your Trials" }),
      ),
    );
  });
});

describe("one authority for a write in flight (#599)", () => {
  // The write used to be decided by a caller-side mark — one slot, tested
  // with `!== null` — beside the reducer that already knew. Three review
  // rounds on #596 produced three spellings of the two disagreeing. The
  // write is now a consequence of the machine entering `saving`, so the
  // three below are one property looked at from three sides.
  const hanging = () => {
    let release: () => void = () => {};
    const record = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = () => resolve();
        }),
    );
    return { record, release: () => release() };
  };

  it("sends another patient's answer while this one's write is in flight", async () => {
    // Spelling two, and the expensive one: B's answer was DISCARDED. Through
    // the three questions their whole ranking went with it.
    const state = fakeState();
    const { record, release } = hanging();
    const store = {
      ...state.adapter,
      weightsWizard: { wasOffered: vi.fn().mockResolvedValue(false), record },
    };
    const { setProps } = renderTrialMatches(fakeApi(), {
      state: store,
      personId: 11,
      patientInfo: null,
    });
    await screen.findByText(OFFER);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));

    setProps({ personId: 22 });
    await screen.findByText(OFFER);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));

    await waitFor(() => expect(record).toHaveBeenCalledTimes(2));
    release();
  });

  it("closes another patient's dialog while this one's write is in flight", async () => {
    // Spelling one. Escape, Close and the scrim all refused for up to
    // WIZARD_WRITE_GRACE_MS on behalf of somebody the reader had left.
    //
    // Already fixed by #596, which stopped `dismissWizard` reading the mark
    // at all — so this is a regression guard rather than a pin on the
    // change below it, and it does not fail when the old mark is restored
    // to the ANSWER handlers only. Kept because the three spellings are one
    // property and a reader chasing the next one should find all three
    // together.
    const state = fakeState();
    const { record, release } = hanging();
    const store = {
      ...state.adapter,
      weightsWizard: { wasOffered: vi.fn().mockResolvedValue(false), record },
    };
    const { setProps } = renderTrialMatches(fakeApi(), {
      state: store,
      personId: 11,
      patientInfo: null,
    });
    await screen.findByText(OFFER);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));

    setProps({ personId: 22 });
    await screen.findByText(OFFER);
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());
    expect(record).toHaveBeenCalledTimes(1); // and 22 wrote nothing
    release();
  });

  // BOTH OF THESE PASS AGAINST THE BASE COMMIT, and that is not a defect in
  // them: the base also wrote synchronously in the handler. What they guard
  // against is the shape this change was nearly made in — a passive effect
  // spending a stashed payload — which fails both. Like the #596 guard
  // below, they are here so the next person to reach for that shape finds
  // out in a second rather than in a review round.
  it("writes in the same task as the click, so a patient switch cannot swallow it", async () => {
    // The write must not wait for the next commit. Moved into a passive
    // effect it did, and anything that changed the patient IN THE SAME TASK
    // as the click then reset the machine before the effect ran: the reader
    // answered, nothing was recorded, and they were asked again. Measured
    // against the commit before that change — 1 write became 0.
    //
    // The WEAKER of the two, measured rather than assumed: the effect-shaped
    // ablation fails both, a macrotask of deferral fails only its sibling
    // below, and one microtask fails neither. An earlier version of this
    // comment had that backwards.
    const { state, record } = withWizard(false);
    const { setProps } = renderTrialMatches(fakeApi(), { state, personId: 11 });
    await screen.findByText(OFFER);
    const skip = screen.getByRole("button", { name: "Keep them equal" });

    await act(async () => {
      skip.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      setProps({ personId: 22 });
    });

    expect(record).toHaveBeenCalledTimes(1);
  });

  it("writes in the same task as the click, so an unmount cannot swallow it", async () => {
    // The other half: a host tearing the remote down in the task that
    // carried the click. Same cause, same loss.
    //
    // The STRONGER of the two: a macrotask of deferral fails this and not
    // its sibling, because a `setTimeout` scheduled before an unmount still
    // runs but React has torn the tree down by then. One microtask fails
    // neither, so neither test claims to catch that.
    const { state, record } = withWizard(false);
    const view = renderTrialMatches(fakeApi(), { state, personId: 11 });
    await screen.findByText(OFFER);
    const skip = screen.getByRole("button", { name: "Keep them equal" });

    await act(async () => {
      skip.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      view.unmount();
    });

    expect(record).toHaveBeenCalledTimes(1);
  });

  it("still answers when a dismissal arrived first in the same task", async () => {
    // Only reachable with scripted events, but it is the shape that says
    // where the permission lives: the dismissal is accepted, so the answer
    // that follows is refused by the machine — and must therefore write
    // NOTHING rather than write and be forgotten. Under the effect it was
    // worse than either: the payload was stashed, the state never reached
    // `saving`, and the payload was silently dropped.
    const { state, record } = withWizard(false);
    renderTrialMatches(fakeApi(), { state, personId: 11 });
    await screen.findByText(OFFER);
    const skip = screen.getByRole("button", { name: "Keep them equal" });

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      skip.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // The dismissal won, so nothing is written — and nothing is left hanging.
    expect(record).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());
  });

  it("answers again on a return visit while the first write is still out", async () => {
    // Spelling three, the one the per-patient narrowing still left open: the
    // reducer resets on the handle move, so coming back to 11 is a NEW
    // question — but the old mark named 11, so every control on that fresh
    // dialog did nothing, with no "Saving…" and nothing disabled to say why.
    const state = fakeState();
    const { record, release } = hanging();
    const store = {
      ...state.adapter,
      weightsWizard: { wasOffered: vi.fn().mockResolvedValue(false), record },
    };
    const { setProps } = renderTrialMatches(fakeApi(), {
      state: store,
      personId: 11,
      patientInfo: null,
    });
    await screen.findByText(OFFER);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));

    setProps({ personId: 22 });
    await screen.findByText(OFFER);
    setProps({ personId: 11 });
    const again = await screen.findByText(OFFER);
    expect(again).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(2));
    release();
  });

  it("does not let the first visit's write close the second visit's dialog", async () => {
    // What `visit` is FOR, at the level where a reader would feel it. The
    // test above only shows the second answer goes out; this one shows the
    // first completion does not land on it. Without the visit in `written`
    // the first settle closes a dialog whose own write is still in the air,
    // and the reader watches "Saving…" vanish over nothing.
    //
    // Two independently releasable writes, because the point is which
    // completion matches which dialog.
    const state = fakeState();
    const releases: Array<() => void> = [];
    const record = vi.fn(
      () => new Promise<void>((resolve) => releases.push(() => resolve())),
    );
    const store = {
      ...state.adapter,
      weightsWizard: { wasOffered: vi.fn().mockResolvedValue(false), record },
    };
    const { setProps } = renderTrialMatches(fakeApi(), {
      state: store,
      personId: 11,
      patientInfo: null,
    });
    await screen.findByText(OFFER);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));

    // Away and back: a second visit, a second question, a second answer.
    setProps({ personId: 22 });
    await screen.findByText(OFFER);
    setProps({ personId: 11 });
    await screen.findByText(OFFER);
    await userEvent.click(screen.getByRole("button", { name: "Keep them equal" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(2));
    // Scoped to the dialog: the page carries other `role="status"` regions.
    const saving = () =>
      within(screen.getByRole("dialog")).queryByText("Saving your answer…");
    expect(saving()).not.toBeNull();

    // The FIRST write finally settles. It belongs to a visit that is gone.
    await act(async () => {
      releases[0]();
    });
    expect(saving()).not.toBeNull();

    // The second visit's own completion is what closes it.
    await act(async () => {
      releases[1]();
    });
    await waitFor(() => expect(screen.queryByText(OFFER)).toBeNull());
  });
});
