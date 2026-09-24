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
    const roll = random();
    if (roll < 0.2) {
      // The host moves to another patient — sometimes the same one, which is
      // what a re-render looks like and must change nothing.
      const before = patient;
      patient = pick(PATIENTS);
      if (patient !== before) issuedForCurrent = 0;
      apply({ kind: "patient", patient });
    } else if (roll < 0.45) {
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
    } else if (roll < 0.7) {
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
    } else if (roll < 0.9) {
      // Usually the reader in front of us; sometimes a stale one, because an
      // answer or a completion can be in flight across a patient switch.
      apply({ kind: "answer", patient: random() < 0.85 ? state.patient : pick(PATIENTS) });
    } else {
      apply({
        kind: "written",
        patient: random() < 0.7 ? state.patient : pick(PATIENTS),
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
      const { log } = drive(seed, 60);
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
      const { log, issuedForCurrent } = drive(seed, 60);
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
      const { stuck } = drive(seed, 60);
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
      const { log } = drive(seed, 60);
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
      const { log } = drive(seed, 60);
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
      const { log } = drive(seed, 60);
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
      const { log } = drive(seed, 60);
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
    // `saving` is entered from `show` alone, so Escape during a write, a
    // double click, or a second answer arriving from anywhere cannot produce
    // a second flag write.
    for (const seed of SEEDS) {
      const { log } = drive(seed, 60);
      for (const { before, after } of log) {
        if (after.at === "saving" && before.at !== "saving") {
          expect({ seed, before }, "entered saving from somewhere other than show")
            .toMatchObject({ before: { at: "show" } });
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
    expect(state).toEqual({ patient: "B", at: "unknown", reading: false });

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
    expect(state).toEqual({ patient: "B", at: "unknown", reading: false });
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

  it("closes on the write settling, and only from saving", () => {
    let state = initialWizard("A");
    state = nextWizard(state, { kind: "reading", patient: "A" });
    state = nextWizard(state, { kind: "read", patient: "A", offered: false });
    // A stray `written` while the question is up must not close it.
    state = nextWizard(state, { kind: "written", patient: "A" });
    expect(state.at).toBe("show");
    state = nextWizard(state, { kind: "answer", patient: "A" });
    state = nextWizard(state, { kind: "written", patient: "A" });
    expect(state.at).toBe("hide");
  });
});
