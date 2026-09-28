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
//  6. THE OFFER IS SPENT ONLY BY AN ANSWER. Spending it means writing the
//     server flag, after which the question never returns for that patient,
//     and exactly two gestures do it: finishing the three questions, and
//     "Keep them equal". Close, Escape and a click on the scrim DISMISS —
//     they take the question off the screen for this visit and state
//     nothing, so nothing is written and the next read asks again. Before
//     #596 those three and "Keep them equal" all went to ONE handler — four
//     of the five, the completed ranking having its own — so a stray click
//     outside the panel was indistinguishable from "keep them equal" and
//     silently spent the one offer the reader was ever going to get. Mechanically, here: `answer` is the
//     only event that reaches `saving`.
//
//     That is HALF of the rule, and the half this file can hold. The write
//     lives in the caller and is gated on its own render-scoped reading of
//     "open and not busy", not on this machine having entered `saving` —
//     dispatch a dismissal and an answer in one task and the flag is written
//     while `saving` is never seen. Measured. So the property over this
//     reducer constrains the MACHINE; what constrains the write is that the
//     only two callbacks reaching `record()` are the two answer handlers,
//     and that is pinned in `weightsWizard.test.tsx` rather than here. A
//     future gesture wired straight to a writing handler would satisfy
//     everything in this file and still be wrong.
//
//     "The next visit" is loose, and the precise statement is "the next
//     READ". A dismissal lasts until `readerHandle` moves, because nothing
//     outside the server remembers it and a move resets this machine. On a
//     payload that NAMES the patient the handle survives a profile refresh
//     (`patientHandleOf` keys on the id), so the reader is not re-asked
//     after an inline edit. On one that names nobody, it moves on every
//     refresh and they are. Remembering a dismissal for a patient who
//     cannot be named would mean remembering it for whoever came next, so
//     that gap stays open on purpose. #600.

/** Where the dialog is for the patient named in the same state.
 *
 *  `hide` is terminal for that patient and covers four different reasons —
 *  already offered, just answered, dismissed without answering, nothing to
 *  ask with. The wizard does not need to tell them apart; the reader sees the
 *  same nothing.
 *
 *  What separates them is not here but on the server, and it is two and two,
 *  not three and one. `already offered` and `just answered` have the flag
 *  written and never come back. A dismissal has not, and comes back on the
 *  next read. Neither has `nothing to ask with` — that is `readFailed`, which
 *  writes nothing at all and is treated as offered only so a question whose
 *  answer we could not read is not asked; a blip on that read retires the
 *  question for this visit and for no longer. Measured.
 *
 *  Do not build on "hide implies the flag is written". Skipping a re-read
 *  after a failed one would spend the offer of a reader who suffered a
 *  network hiccup. */
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
  /** The reader answered — chose "Keep them equal", or finished the three
   *  questions. An answer is written; see rule 6. */
  | { kind: "answer"; patient: string }
  /** The reader took the dialog off the screen without answering it — Close,
   *  Escape, a click on the scrim. Nothing is written, so the offer survives
   *  to the next visit; see rule 6. */
  | { kind: "dismissed"; patient: string }
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
      // Only from `show`. Anything else is a second answer — a double click,
      // a click arriving behind one already sent — and there is only one flag
      // to write. This is also the ONLY case that reaches `saving`, which is
      // rule 6 stated where it is enforced rather than only in the header:
      // the caller writes from `saving` and from nowhere else, so a gesture
      // that cannot produce this event cannot spend the offer.
      return event.patient === state.patient && state.at === "show"
        ? { ...state, at: "saving" }
        : state;

    case "dismissed":
      // Straight to `hide`, never through `saving`: nothing is written, so
      // there is nothing to wait for. Only from `show`, for the same reason
      // `answer` is — while a write is in the air the dialog is sealed, and
      // a dismissal arriving behind an answer must not be read as undoing it.
      //
      // Deliberately NOT merged with `answer` plus a flag on the caller's
      // side. The difference between the two is the whole of #596, and a
      // shared event would put it back in the caller's hands, where it was
      // when a scrim click spent the offer.
      return event.patient === state.patient && state.at === "show"
        ? { ...state, at: "hide" }
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
