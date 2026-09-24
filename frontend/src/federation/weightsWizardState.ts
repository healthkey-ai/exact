// Whether the weights wizard is on screen, and whose answer it is.
//
// This is four lines of state that took three review rounds, because each
// round fixed one SPELLING of the same invariant instead of the invariant:
// the read was keyed on the adapter object, then on a hash of the patient's
// whole payload, then on a mark that nothing ever cleared — and each fix
// broke the next case along. So the rules are written down here, once, as a
// pure reducer that `weightsWizardState.test.ts` can drive through every
// interleaving instead of the four a component test can reach.
//
// The rules, in the order they were learned:
//
//  1. Every fact here belongs to ONE patient. A read answers for the patient
//     it was issued for and for nobody else, and a view showing patient B
//     must never be able to display, or record, an answer about patient A.
//  2. "Nothing known yet" and "a read is out" are different, and conflating
//     them is what issued one GET per render against a host that rebuilds its
//     adapter inline. Hence `reading` beside `at`, not a fifth value of `at`.
//  3. A patient who has never been asked must eventually be asked. A guard
//     that can silently drop the only read they will ever get is worse than
//     the duplicate read it prevents — that was the third round's P1: the
//     answer for the new patient arrived, was discarded because the state
//     still named the old one, and no second read was allowed.
//  4. An answer, once given, stands FOR THAT PATIENT AND THIS VISIT. No read
//     may put the question back on screen after the reader has dealt with it,
//     however late it lands. The scope is deliberate and rule 1 outranks it:
//     A → B → A starts A over, because nothing here can tell "the same person
//     again" from "a person we have not met", and the flag on the server is
//     the thing that actually remembers. If A's write has not been reflected
//     yet, A is asked once more — the cost this feature pays everywhere.
//  5. Every event names the patient it is about, completions included. A
//     write settling for the patient the reader has just left must not close
//     the dialog of the one they are looking at now.

/** Where the dialog is for the patient named in the same state.
 *
 *  `hide` is terminal for that patient and covers three different reasons —
 *  already offered, just answered, nothing to ask with. The wizard does not
 *  need to tell them apart; the reader sees the same nothing. */
export type WizardAt = "unknown" | "show" | "saving" | "hide";

export interface WizardState {
  /** The patient every other field is about. A handle, not a view key: the
   *  host re-reads the same person's profile and hands over a new payload,
   *  and an answer must survive that. */
  readonly patient: string;
  readonly at: WizardAt;
  /** A read has gone out for `patient` and has not come back. Not a value of
   *  `at`, because the dialog looks identical either way — this exists only
   *  to stop a second read, and `at === "unknown"` cannot say it. */
  readonly reading: boolean;
}

export type WizardEvent =
  /** The view is now showing this patient. */
  | { kind: "patient"; patient: string }
  /** A flag read has just been issued for this patient. */
  | { kind: "reading"; patient: string }
  /** That read answered. */
  | { kind: "read"; patient: string; offered: boolean }
  /** …or did not. Treated as "already offered": asking a question whose
   *  answer we have just failed to read is asking one we cannot record. */
  | { kind: "readFailed"; patient: string }
  /** The reader answered — declined, or finished the three questions. */
  | { kind: "answer"; patient: string }
  /** The write that answer produced has settled, landed or not. */
  | { kind: "written"; patient: string };

export const initialWizard = (patient: string): WizardState => ({
  patient,
  at: "unknown",
  reading: false,
});

/** Whether a read may be issued now.
 *
 *  `ready` is the caller's own gate — a store to read through, and the saved
 *  filters in hand, so the question does not land over a list still settling. */
export const shouldRead = (state: WizardState, ready: boolean): boolean =>
  ready && state.at === "unknown" && !state.reading;

/** Whether the dialog is on screen for the patient the caller is showing. */
export const isOpen = (state: WizardState, patient: string): boolean =>
  state.patient === patient && (state.at === "show" || state.at === "saving");

export function nextWizard(state: WizardState, event: WizardEvent): WizardState {
  switch (event.kind) {
    case "patient":
      // A new patient is a new question, from nothing. Rule 1: none of the
      // old patient's state may be read as the new one's — including the
      // ANSWER, which is why this resets rather than merging. Rule 3 rides on
      // the same line: without it the new patient's read lands against a
      // state still naming the old one and is thrown away.
      return event.patient === state.patient ? state : initialWizard(event.patient);

    case "reading":
      // Recorded, not assumed. The caller checks `shouldRead` and then says
      // it did — two steps, because under StrictMode the effect runs twice
      // and the second run has to see the first one's mark.
      return event.patient === state.patient && shouldRead(state, true)
        ? { ...state, reading: true }
        : state;

    case "read":
    case "readFailed": {
      // Rule 1: not this patient, not this state's business. Rule 4: the
      // reader may have answered while this was in the air, and their answer
      // outranks it.
      if (event.patient !== state.patient || state.at !== "unknown") return state;
      const offered = event.kind === "readFailed" || event.offered;
      return { ...state, at: offered ? "hide" : "show", reading: false };
    }

    case "answer":
      // Only from `show`. Anything else is a second answer — Escape while the
      // first is on the wire, a double click — and there is only one flag to
      // write.
      return event.patient === state.patient && state.at === "show"
        ? { ...state, at: "saving" }
        : state;

    case "written":
      // Named, like everything else here, and for a reason that is not
      // symmetry: A's write can settle after the host has moved to B and B
      // has answered, and an unscoped completion then closed B's dialog on
      // A's behalf — before B's own write had been anywhere.
      return event.patient === state.patient && state.at === "saving"
        ? { ...state, at: "hide" }
        : state;
  }
}
