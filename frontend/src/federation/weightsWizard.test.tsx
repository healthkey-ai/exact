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
    // answering", which Escape cannot say — Escape declines, and that is
    // recorded.
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
  it("does not take Escape as a decline while an answer is on the wire", async () => {
    // `Dialog` dismisses on Escape, on its own Close button and on the scrim,
    // and `busy` reaches none of them — it disables the wizard's buttons and
    // nothing else. So Escape after the third question recorded a DECLINE
    // over a ranking still in flight, by the one path that skips the check
    // that the ranking saved.
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
    // user back on Close, which is the permanent decline.
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
    render(<WeightsWizard busy onDecline={vi.fn()} onFinish={vi.fn()} />);

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
      <WeightsWizard onDecline={vi.fn()} onFinish={vi.fn()} />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Answer three questions" }),
    );
    await screen.findByText(/most important factor when choosing/);
    expect(screen.queryByRole("status")).toBeNull();

    rerender(<WeightsWizard busy onDecline={vi.fn()} onFinish={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Saving your answer…");
  });

  it("refuses Escape and Close on its own, without help from the caller", async () => {
    // Two guards stand between a dismissal and a recorded decline: this one,
    // and `TrialMatches`'s refusal to record while saving. Each covers the
    // other, so neither shows up when the other is removed — which is why
    // this one is pinned here, away from the caller.
    const onDecline = vi.fn();
    render(<WeightsWizard busy onDecline={onDecline} onFinish={vi.fn()} />);

    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onDecline).not.toHaveBeenCalled();

    // And the guard is about `busy`, not about the dialog: not busy, it goes.
    cleanup();
    const second = vi.fn();
    render(<WeightsWizard onDecline={second} onFinish={vi.fn()} />);
    await userEvent.keyboard("{Escape}");
    expect(second).toHaveBeenCalledTimes(1);
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
  it("records the decline once, however many dismissals arrive together", async () => {
    // The reducer refuses the second `answer`, but the WRITE belongs to the
    // widget and it cannot see that refusal: `wizardOpen` and `wizardBusy`
    // are render-scoped, so two dismissals dispatched in the same task both
    // read "open, not busy". Not reachable through the UI today; it is the
    // next control added to the panel that would find it.
    const { state, record } = withWizard(false);
    renderTrialMatches(fakeApi(), { state });
    await screen.findByText(OFFER);

    // Three inside ONE `act`, dispatched straight at the document. Each
    // `fireEvent` wraps itself in its own `act`, so a render lands between
    // them and the second one already sees `busy` — which is the very
    // render-scoped reading this is about, and would make the test agree with
    // the bug.
    await act(async () => {
      for (let i = 0; i < 3; i += 1) {
        document.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
        );
      }
    });

    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(record).toHaveBeenCalledTimes(1);
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
