// The wizard's state machine, driven through interleavings a component test
// cannot reach.
//
// Three review rounds found three P1s in these four lines, each a different
// spelling of one rule about whose answer is on screen. Every one of them was
// "here is a sequence you did not think of", which is what property testing
// is for — so the sequences are generated here rather than enumerated, and
// the harness stays in the repo.
//
// The generator is a seeded LCG rather than a library: this repo has no
// property-testing dependency, adding one for a single module is a bigger
// decision than this file, and a failure has to be reproducible from its seed
// to be worth anything. Every run covers the same 400 sequences.
import { describe, expect, it } from "vitest";

import {
  initialWizard,
  isOpen,
  nextWizard,
  shouldRead,
  type WizardEvent,
  type WizardState,
} from "./weightsWizardState";

/** Numbers from a seed, so a failure names the sequence that produced it. */
const randomFrom = (seed: number) => {
  let value = seed >>> 0;
  return () => {
    // Numerical Recipes' LCG. Not for anything that needs to be unguessable.
    value = (value * 1664525 + 1013904223) >>> 0;
    return value / 0x100000000;
  };
};

const PATIENTS = ["A", "B", "C"];

/** How often each kind of step happens, as weights rather than as a chain of
 *  cumulative `roll <` thresholds.
 *
 *  The chain was the original shape, and it has a failure mode this file met:
 *  #596 added a sixth kind by carving its span out of `answer`'s, which took
 *  a quarter of the answer events away from the five properties that already
 *  quantified over them. Nothing said so, and the comment on the new branch
 *  claimed the two rates were equal when they were three times apart. Weights
 *  ADD to the total instead, so an existing kind's share can only change if
 *  somebody edits its number, and `STEPS_PER_RUN` is scaled with the total so
 *  the expected count per run of each original kind is unchanged. */
const WEIGHTS = {
  patient: 20,
  gate: 25,
  read: 25,
  answer: 20,
  dismissed: 20,
  written: 10,
} as const;

type StepKind = keyof typeof WEIGHTS;

const TOTAL_WEIGHT = Object.values(WEIGHTS).reduce((sum, w) => sum + w, 0);

/** 60 steps carried the original 100 units of weight. At 120 the same 72. */
const STEPS_PER_RUN = Math.round((60 * TOTAL_WEIGHT) / 100);

const pickKind = (roll: number): StepKind => {
  let at = roll * TOTAL_WEIGHT;
  for (const kind of Object.keys(WEIGHTS) as StepKind[]) {
    at -= WEIGHTS[kind];
    if (at < 0) return kind;
  }
  // Only reachable if `roll` is exactly 1, which this generator never
  // produces; the last kind is the honest answer either way.
  return "written";
};

/** One run of the machine, and everything that happened to it. */
function drive(seed: number, steps: number) {
  const random = randomFrom(seed);
  const pick = <T,>(items: readonly T[]) => items[Math.floor(random() * items.length)];

  let patient = PATIENTS[0];
  let state = initialWizard(patient);
  const log: Array<{
    event: WizardEvent;
    before: WizardState;
    after: WizardState;
    /** Reads in the air at that moment, as the RUN knows them — the model's
     *  own `reading` is what gets checked against this. */
    outstanding: string[];
  }> = [];
  // Reads that have been issued and not yet answered, oldest first. A real
  // one can answer late, out of order, or after its patient is gone, so the
  // generator is allowed to answer any of them at any point.
  const outstanding: string[] = [];

  // What the model claims, checked against what actually happened: how many
  // reads this run has issued for the current patient since it last arrived,
  // and which reads are genuinely still in the air.
  let issuedForCurrent = 0;
  /** Times the run wanted to read, could have, and was refused. A patient in
   *  that position is never asked at all. */
  let stuck = 0;
  const outstandingFor = (who: string) => outstanding.filter((p) => p === who).length;

  const apply = (event: WizardEvent) => {
    const before = state;
    state = nextWizard(state, event);
    log.push({ event, before, after: state, outstanding: [...outstanding] });
  };

  for (let step = 0; step < steps; step += 1) {
    const kind = pickKind(random());
    if (kind === "patient") {
      // The host moves to another patient — sometimes the same one, which is
      // what a re-render looks like and must change nothing.
      const before = patient;
      patient = pick(PATIENTS);
      if (patient !== before) issuedForCurrent = 0;
      apply({ kind: "patient", patient });
    } else if (kind === "gate") {
      // The caller's own gate opens and closes: a store appears, the saved
      // filters go pending again.
      const ready = random() < 0.8;
      if (shouldRead(state, ready)) {
        outstanding.push(state.patient);
        issuedForCurrent += 1;
        apply({ kind: "reading", patient: state.patient });
      } else if (
        ready &&
        state.at === "unknown" &&
        outstandingFor(state.patient) === 0
      ) {
        // Nothing is known about this patient, nothing is on its way, the
        // caller is ready — and the model says no. That patient is never
        // asked. Counted rather than thrown so the property, not the driver,
        // reports it.
        stuck += 1;
      }
    } else if (kind === "read") {
      // A read answers. Usually one that is outstanding; sometimes one that
      // is not, because a read issued before a patient switch is still in the
      // air afterwards and the machine has to be right about a straggler it
      // is no longer expecting. Nothing outside this module can stop one
      // arriving, so nothing here may assume it cannot.
      const answering =
        outstanding.length && random() < 0.85
          ? outstanding.splice(Math.floor(random() * outstanding.length), 1)[0]
          : pick(PATIENTS);
      apply(
        random() < 0.15
          ? { kind: "readFailed", patient: answering }
          : { kind: "read", patient: answering, offered: random() < 0.5 },
      );
    } else if (kind === "answer") {
      // Usually the reader in front of us; sometimes a stale one, because an
      // answer or a completion can be in flight across a patient switch.
      apply({ kind: "answer", patient: random() < 0.85 ? state.patient : pick(PATIENTS) });
    } else if (kind === "dismissed") {
      // Close, Escape, the scrim. Weighted the same as an answer and drawn
      // against the same mix of stale patients, because the machine has to
      // be as right about a dismissal landing late as about an answer — and
      // because the property below is only worth anything if dismissals are
      // tried from every state an answer is tried from.
      apply({
        kind: "dismissed",
        patient: random() < 0.85 ? state.patient : pick(PATIENTS),
      });
    } else {
      // Usually this visit's, sometimes an older one — a write started
      // before a patient switch can settle after the reader has come BACK,
      // which is the case `visit` exists for and the one a generator that
      // always quotes the current number would never produce.
      apply({
        kind: "written",
        patient: random() < 0.7 ? state.patient : pick(PATIENTS),
        visit: random() < 0.75 ? state.visit : Math.max(0, state.visit - 1),
      });
    }
  }
  return { log, state, patient, stuck, issuedForCurrent };
}

const SEEDS = Array.from({ length: 400 }, (_, i) => i * 7919 + 1);

describe("the wizard state machine, over generated event sequences", () => {
  it("never shows one patient's question to another", () => {
    // Rule 1, and the one with a reader on the other end of it: the dialog is
    // open only for the patient it names, and only because a read for THAT
    // patient said they had not been asked.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      for (const { event, before, after } of log) {
        if (after.at !== "show" || before.at === "show") continue;
        // It just opened. The only event that may open it is that patient's
        // own read coming back "not offered".
        expect(
          { seed, event, before, after },
          "the dialog opened on something other than this patient's own read",
        ).toMatchObject({
          event: { kind: "read", patient: after.patient, offered: false },
        });
      }
    }
  });

  it("issues at most one read per patient, per visit to that patient", () => {
    // Rule 2. Without it a host rebuilding its adapter inline issued one GET
    // per render — measured at four reads across three re-renders — and a
    // host that re-renders continuously never finished a read at all.
    // Counted as reads the run ISSUED, not as `reading` going false→true.
    // The transition count misses exactly the mutation this rule is about: a
    // `shouldRead` that ignores the mark lets the caller fire again and
    // again, and every one of those finds `before.reading` already true, so
    // the transition never happens and the count stays at one.
    for (const seed of SEEDS) {
      const { log, issuedForCurrent } = drive(seed, STEPS_PER_RUN);
      let issued = 0;
      for (const { event, before, after, outstanding } of log) {
        if (event.kind === "patient" && after.patient !== before.patient) issued = 0;
        if (event.kind === "reading" && outstanding.includes(event.patient)) {
          issued += 1;
        }
        expect({ seed, event }, "a second read for the same patient").toSatisfy(
          () => issued <= 1,
        );
      }
      expect({ seed }, "the run itself issued more than one").toSatisfy(
        () => issuedForCurrent <= 1,
      );
    }
  });

  it("lets every patient who has not been asked be asked", () => {
    // Rule 3 — the third round's P1.
    //
    // The first version of this asked `shouldRead(after, true)` on states it
    // had already filtered to `at === "unknown" && !reading`, which IS
    // `shouldRead`: a tautology that passed against all thirteen reducer
    // mutations, including one where a patient change left `reading` set and
    // the new patient could never be read. The rule needs the WORLD to check
    // the model against, so the driver keeps the books and reports how often
    // it was refused a read it should have got.
    for (const seed of SEEDS) {
      const { stuck } = drive(seed, STEPS_PER_RUN);
      expect(
        { seed, stuck },
        "a patient was ready to be asked and the model refused",
      ).toSatisfy(() => stuck === 0);
    }
  });

  it("never claims a read is in the air when none is", () => {
    // The other half of rule 3, and the shape the tautology was hiding: the
    // mark is what refuses a read, so a mark nobody will ever clear is the
    // same thing as never asking. Checked against the run's own record of
    // what it actually issued.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      for (const { event, after, outstanding } of log) {
        if (!after.reading) continue;
        expect(
          { seed, event, after, outstanding },
          "the model is waiting for a read that was never issued",
        ).toSatisfy(() => outstanding.includes(after.patient));
      }
    }
  });

  it("lets a read decide only while nothing is known", () => {
    // Rules 1 and 4 as one statement, which is how they should have been
    // written in the first place: a read is the answer to "has this patient
    // been asked", and that question is open exactly once. Once anything else
    // has settled it — the reader answering, an earlier read — a later one is
    // stale by definition, whoever it names.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      for (const { event, before, after } of log) {
        if (event.kind !== "read" && event.kind !== "readFailed") continue;
        if (before.at === "unknown" && event.patient === before.patient) continue;
        expect(
          { seed, event, before, after },
          "a read moved a state that was no longer asking",
        ).toSatisfy(() => after === before);
      }
    }
  });

  it("lets only the patient who answered close their own dialog", () => {
    // Rule 5. A's write settling after the host has moved to B, with B
    // mid-answer, closed B's dialog on A's behalf — before B's own write had
    // been anywhere.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      for (const { event, before, after } of log) {
        if (event.kind !== "written" && event.kind !== "answer") continue;
        if (event.patient === before.patient) continue;
        expect(
          { seed, event, before, after },
          "another patient's answer or completion moved this one",
        ).toSatisfy(() => after === before);
      }
    }
  });

  it("never puts the question back after the reader has answered", () => {
    // Rule 4. A read that resolves late must not reopen a dialog the reader
    // has already declined or completed.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      const answered = new Set<string>();
      for (const { event, before, after } of log) {
        if (event.kind === "patient" && after.patient !== before.patient) {
          // A different patient is a different question, legitimately.
          answered.delete(after.patient);
        }
        if (event.kind === "answer" && after.at === "saving") answered.add(after.patient);
        if (answered.has(after.patient)) {
          expect(
            { seed, event, after },
            "an answered patient was asked again",
          ).toSatisfy(() => after.at !== "show");
        }
      }
    }
  });

  it("only ever writes one answer per patient", () => {
    // `saving` is entered from `show` alone, so a second click during a
    // write, or a second answer arriving from anywhere, cannot produce a
    // second flag write.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      for (const { before, after } of log) {
        if (after.at === "saving" && before.at !== "saving") {
          expect({ seed, before }, "entered saving from somewhere other than show")
            .toMatchObject({ before: { at: "show" } });
        }
      }
    }
  });

  it("never lets an older visit's completion close this one", () => {
    // #599. The generator quotes a stale visit a quarter of the time, and
    // without this nothing looked at the outcome — ablating the check in
    // the reducer failed one hand-written test and none of these. Measured
    // over these seeds: 19 completions arrive for this patient, at
    // `saving`, quoting a visit that is not the current one. Few, because
    // the state has to be `saving` at the moment one lands, and enough that
    // the branch is exercised rather than asserted into the void.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      for (const { event, before, after } of log) {
        if (event.kind !== "written") continue;
        if (event.patient !== before.patient || before.at !== "saving") continue;
        if (event.visit === before.visit) continue;
        expect(
          { seed, event, before, after },
          "a completion from another visit closed this one's dialog",
        ).toSatisfy(() => after === before);
      }
    }
  });

  it("spends the offer only on an answer, never on a dismissal", () => {
    // Rule 6, and the whole of #596. `saving` is the only state the caller
    // writes the flag from, so "which gestures can spend the one offer this
    // feature has" reduces to "which events can reach `saving`" — and the
    // answer has to be exactly one of them, over every interleaving, not
    // just the four a component test can reach.
    //
    // Stated over the event rather than over the state deliberately. The
    // previous bug was not a wrong transition; it was the caller sending
    // `answer` for a gesture that had not answered anything. A property that
    // only checked `before.at === "show"` would have passed throughout.
    //
    // Not vacuous, re-measured over these seeds after #599 added `visit`
    // (which makes the machine sit in `saving` longer, so every number
    // moved): 235 entries into `saving` to quantify over, and 4775
    // dismissals attempted against them. Of those, the three states that
    // could go wrong are reached 264 times from `show` with a matching
    // patient, 212 against the seal and 493 naming a patient who has moved
    // on; the remaining 3806 are this patient at `unknown` or `hide`, where
    // there is nothing to dismiss.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      for (const { event, before, after } of log) {
        if (after.at === "saving" && before.at !== "saving") {
          expect(
            { seed, event, before },
            "something other than an answer reached saving",
          ).toMatchObject({ event: { kind: "answer" } });
        }
      }
    }
  });

  it("leaves a dismissed patient asked again, by writing nothing", () => {
    // The other half of rule 6, and the half a reader actually feels. A
    // dismissal must land on `hide` — off the screen for this visit — and it
    // must never pass through `saving`, because passing through `saving` is
    // how the caller learns to write.
    for (const seed of SEEDS) {
      const { log } = drive(seed, STEPS_PER_RUN);
      for (const { event, before, after } of log) {
        if (event.kind !== "dismissed") continue;
        const acted = before.patient === event.patient && before.at === "show";
        if (acted) {
          expect(
            { seed, event, before, after },
            "a dismissal did not close the question",
          ).toMatchObject({ after: { at: "hide" } });
        } else {
          // Identity, not `after.at === before.at`, which is what rule 5's
          // property next door asserts and for the same reason: a dismissal
          // that has no business acting must return the state untouched, and
          // comparing one field would let it clear `reading` or rewrite
          // `patient` on the way past. Measured over these seeds: 4511 of
          // the 4775 generated dismissals take this branch, so it carries
          // most of the coverage.
          expect(
            { seed, event, before, after },
            "a dismissal that should not have acted changed the state",
          ).toSatisfy(() => after === before);
        }
      }
    }
  });
});

describe("the transitions that carried a defect", () => {
  it("resets for a new patient, so their read is not thrown away", () => {
    // Round 3's P1, spelled out. A answered; B has never been asked. Without
    // the reset, B's read landed against a state still naming A, was
    // discarded by the key check, and no second read was allowed.
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: true });
    expect(state.at).toBe("hide");

    state = nextWizard(state, { kind: "patient", patient: "B" });
    // `visit: 1` rather than a looser match: the reset bumping it is what
    // lets a completion name the arrival it belongs to, and an exact
    // comparison is the thing that would notice if a reset stopped doing it.
    expect(state).toEqual({ patient: "B", at: "unknown", reading: false, visit: 1 });

    state = nextWizard(state, { kind: "reading", patient: "B" });
    state = nextWizard(state, { kind: "read", patient: "B", offered: false });
    expect(isOpen(state, "B")).toBe(true);
  });

  it("ignores a read for the patient who is no longer on screen", () => {
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "patient", patient: "B" });
    // A's read lands late, and says A had never been asked.
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    expect(isOpen(state, "B")).toBe(false);
    // `visit: 1` rather than a looser match: the reset bumping it is what
    // lets a completion name the arrival it belongs to, and an exact
    // comparison is the thing that would notice if a reset stopped doing it.
    expect(state).toEqual({ patient: "B", at: "unknown", reading: false, visit: 1 });
  });

  it("treats the same patient arriving again as a re-render, not a reset", () => {
    // The host hands over a new payload object for the same person. Round
    // 2's P1: keyed on that, the answer was discarded and the question came
    // back on top of the write that was still in flight.
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    state = nextWizard(state, { kind: "answer", patient: "A" });
    expect(state.at).toBe("saving");

    state = nextWizard(state, { kind: "patient", patient: "A" });
    expect(state.at).toBe("saving");
  });

  it("does not let a second effect run issue a second read", () => {
    // StrictMode invokes the mount effect twice. The first run's mark has to
    // be visible to the second.
    let state = initialWizard("A");
    expect(shouldRead(state, true)).toBe(true);
    state = nextWizard(state, { kind: "reading", patient: "A" });
    expect(shouldRead(state, true)).toBe(false);
    state = nextWizard(state, { kind: "reading", patient: "A" });
    expect(state.reading).toBe(true);
  });

  it("refuses a read mark once the answer is known", () => {
    // The reducer checks `shouldRead` again rather than trusting the caller.
    // The generated runs cannot exercise that — the driver asks first, as the
    // widget does — so it is stated here. It is the StrictMode defence: two
    // effect runs, one mark, and the second must not reopen the window.
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: true });
    const settled = state;
    expect(nextWizard(state, { kind: "reading", patient: "A" })).toBe(settled);
  });

  it("clears the read mark when the read answers", () => {
    // Nothing consumes it afterwards today — `reading` is only ever read
    // while `at` is `unknown`, and the way back is a reset. It is cleared
    // anyway because the state is the module's public shape and a state that
    // says "still waiting" about a read that came back is a lie a debugger
    // will believe.
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    expect(state.reading).toBe(true);
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    expect(state.reading).toBe(false);
  });

  it("holds the question until the caller is ready", () => {
    const state = initialWizard("A");
    expect(shouldRead(state, false)).toBe(false);
    expect(shouldRead(state, true)).toBe(true);
  });

  it("closes a dismissal straight out, and only from show", () => {
    // #596. Close, Escape and the scrim take the question off the screen
    // without writing anything, so the machine has to reach `hide` WITHOUT
    // passing through `saving` — that state is the caller's signal to write.
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    expect(state.at).toBe("show");

    // Somebody else's dismissal is not this patient's business.
    expect(nextWizard(state, { kind: "dismissed", patient: "B" })).toBe(state);

    const dismissed = nextWizard(state, { kind: "dismissed", patient: "A" });
    expect(dismissed.at).toBe("hide");

    // Sealed while an answer is on the wire, exactly as `answer` is: a
    // dismissal arriving behind one must not be read as undoing it.
    const saving = nextWizard(state, { kind: "answer", patient: "A" });
    expect(saving.at).toBe("saving");
    expect(nextWizard(saving, { kind: "dismissed", patient: "A" })).toBe(saving);

    // And nothing to dismiss once it is gone.
    expect(nextWizard(dismissed, { kind: "dismissed", patient: "A" })).toBe(dismissed);
  });

  it("asks a dismissed patient again the next time they arrive", () => {
    // The visit scope, from the reducer's side. Nothing here remembers a
    // dismissal across a patient switch, and nothing should: the server flag
    // is the only thing that remembers, and a dismissal does not write it.
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    state = nextWizard(state, { kind: "dismissed", patient: "A" });
    expect(isOpen(state, "A")).toBe(false);

    state = nextWizard(state, { kind: "patient", patient: "B" });
    state = nextWizard(state, { kind: "patient", patient: "A" });
    expect(shouldRead(state, true)).toBe(true);
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    expect(isOpen(state, "A")).toBe(true);
  });

  it("closes on the write settling, and only from saving", () => {
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    // A stray `written` while the question is up must not close it.
    state = nextWizard(state, { kind: "written", patient: "A", visit: state.visit });
    expect(state.at).toBe("show");
    state = nextWizard(state, { kind: "answer", patient: "A" });
    state = nextWizard(state, { kind: "written", patient: "A", visit: state.visit });
    expect(state.at).toBe("hide");
  });

  it("does not let an earlier visit's completion close this one's dialog", () => {
    // #599. A answers, the reader goes to B and comes back, and A is asked
    // again because the first write has not landed and the flag still reads
    // no. Both writes are harmless — the flag is the same, the weights are
    // last-wins — but the FIRST one settling must not close the SECOND
    // one's dialog, out from under a write still in the air.
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    state = nextWizard(state, { kind: "answer", patient: "A" });
    const firstVisit = state.visit;
    expect(state.at).toBe("saving");

    // Away and back: a new visit, a new question, a second answer.
    state = nextWizard(state, { kind: "patient", patient: "B" });
    state = nextWizard(state, { kind: "patient", patient: "A" });
    expect(state.visit).toBeGreaterThan(firstVisit);
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    state = nextWizard(state, { kind: "answer", patient: "A" });
    expect(state.at).toBe("saving");

    // The first write finally settles. Same patient, older visit.
    state = nextWizard(state, { kind: "written", patient: "A", visit: firstVisit });
    expect(state.at).toBe("saving");

    // And this visit's own completion does close it.
    state = nextWizard(state, { kind: "written", patient: "A", visit: state.visit });
    expect(state.at).toBe("hide");
  });
});
