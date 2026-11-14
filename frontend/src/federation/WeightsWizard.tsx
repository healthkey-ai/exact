// The offer, and the three questions behind it.
//
// The Suitability Score weighs four things, equally until somebody says
// otherwise. Almost nobody says otherwise: the numeric form that sets them
// asks for four numbers out of 100, which is a question about arithmetic
// rather than about the reader's life. Ranking three of them is the same
// question asked in a way a person can answer.
//
// CB asks it during signup and does not take no for an answer — steps 11-13
// of fourteen, reappearing over every page until finished. Here it is one
// offer, on the page the weights affect, and declining is an answer that is
// remembered. So the three steps are CB's, verbatim (`weightRanking.ts`), and
// the offer around them is not, because CB has none.
//
// One deviation inside the steps, declared rather than accidental: CB draws
// each factor as a radio with a `selected` state and a separate Next, so Back
// shows which one was picked. This is click-to-advance, so Back shows an
// unmarked list. Porting the radio would mean porting the Next button and a
// second click per question for a three-question form — the port rule is
// about not IMPROVING a port silently, and this is the same three questions
// with one control instead of two, noted here so the next reader comparing
// the two wizards does not read it as drift.
import { useEffect, useRef, useState } from "react";

import { Dialog } from "./Dialog";
import { STEPS, remaining, weightsFor } from "./weightRanking";
import type { WeightKey } from "./weights";
import type { FilterState } from "./types";

export interface WeightsWizardProps {
  /** "Keep them equal" — an answer, and recorded as one. The offer is made
   *  once, and this is one of the two gestures that spends it. */
  onDecline: () => void;
  /** Close, Escape, a click on the scrim. NOT an answer: the question leaves
   *  the screen for this visit and nothing is recorded, so the reader is
   *  asked again next time (#596). Separate from `onDecline` because the two
   *  used to be the same call, which made a stray click outside the panel
   *  spend the one offer this feature has. */
  onDismiss: () => void;
  /** The ranking, as weights. One call, at the end. */
  onFinish: (weights: FilterState) => void;
  /** A write is on the wire. Both buttons wait for it rather than letting a
   *  second click send a second answer. */
  busy?: boolean;
}

/** What is happening while every control is disabled.
 *
 *  On BOTH screens, not just the offer. The first version put the word on the
 *  offer's primary button — which is the FAST path, one unconditional PATCH.
 *  The slow one is finishing the three questions: a 400ms debounce flush, a
 *  write, and possibly a refusal, a re-read and a retry. That screen has no
 *  primary button to relabel, so it showed nothing at all, and `Dialog`'s
 *  focus trap — which filters disabled controls — left the reader cycling on
 *  a Close button whose handler is a no-op. Indistinguishable from a hung
 *  dialog, which is exactly what this was meant to prevent.
 *
 *  `role="status"` so it is announced without stealing focus; `aria-busy` on
 *  the panel says the same thing structurally. */
const Saving = () => (
  <p className="exact-wizard__saving" role="status">
    Saving your answer…
  </p>
);

export function WeightsWizard({ onDecline, onDismiss, onFinish, busy }: WeightsWizardProps) {
  // `Dialog` moves focus in once, onto its Close button, and never again —
  // its effect has `[]` deps and React reconciles every screen below to the
  // same instance. So choosing a factor unmounted the focused button and
  // dropped focus to `document.body`: a screen reader was told nothing about
  // the new question, and the reader's way back in was Tab, which lands on
  // Close — the permanent decline. Focus moves to the heading instead, on
  // open and on every step, so the question is announced and the first tab
  // stop is an answer rather than a refusal.
  const heading = useRef<HTMLHeadingElement>(null);
  // `null` is the offer; 0-2 are the questions. Not a boolean plus an index:
  // the offer is a different question from "which factor", and folding them
  // into one counter made the Back button walk into a step that does not
  // exist.
  const [step, setStep] = useState<number | null>(null);
  const [ranked, setRanked] = useState<WeightKey[]>([]);

  useEffect(() => {
    heading.current?.focus();
  }, [step]);

  const choose = (key: WeightKey) => {
    const at = step ?? 0;
    // Everything after this place is dropped. Changing an earlier answer
    // makes the later ones stale — CB clears them the same way — and leaving
    // them would let a reader rank the same factor twice by going back.
    const next = [...ranked.slice(0, at), key];
    setRanked(next);
    if (next.length === STEPS.length) onFinish(weightsFor(next));
    else setStep(at + 1);
  };

  // Escape, the scrim and `Dialog`'s own Close button all reach `onClose`,
  // and none of them is an answer. They went to `onDecline` until #596, which
  // made the one gesture a reader makes BY ACCIDENT — a click that lands
  // outside the panel — spend an offer that is never made twice. The reader
  // saw the dialog vanish, chose nothing, and could not get it back.
  //
  // The line is drawn at what a gesture says, not at how deliberate it looks.
  // The button is labelled "Close"; Escape is its keyboard spelling; the
  // scrim is not labelled at all. None of the three says "keep them equal" —
  // that button does, and it is still `onDecline`.
  //
  // `busy` guards this because it never did: it disables the buttons in here
  // and nothing else. A reader could answer the third question and then press
  // Escape while the write was still out, which recorded a decline over their
  // answer. While a write is in the air the dialog does not close; it is
  // about to close itself.
  const dismiss = () => {
    if (!busy) onDismiss();
  };

  // Each screen's accessible name is the question ON it, not a standing title
  // for the flow. They were different — "Set your trial priorities" against a
  // visible "What matters most to you?" — which leaves a screen-reader user
  // hearing one thing named and reading another, and the dialog's name is the
  // only announcement they get when focus enters it.
  if (step === null) {
    return (
      <Dialog label="What matters most to you?" onClose={dismiss}>
        <div className="exact-wizard" aria-busy={busy || undefined}>
          <h2 className="exact-wizard__title" tabIndex={-1} ref={heading}>
            What matters most to you?
          </h2>
          <p className="exact-wizard__hint">
            Trials are ranked by four things: the benefit they might bring, the
            risk, what taking part asks of you, and how far you would travel.
            They currently count equally. Three questions will weight them the
            way you would.
          </p>
          <div className="exact-wizard__actions">
            <button
              type="button"
              className="exact-wizard__go"
              onClick={() => setStep(0)}
              disabled={busy}
            >
              Answer three questions
            </button>
            <button
              type="button"
              className="exact-wizard__skip"
              onClick={onDecline}
              disabled={busy}
            >
              Keep them equal
            </button>
          </div>
          <p className="exact-wizard__hint exact-wizard__hint--small">
            You can change these at any time under Suitability Preferences.
          </p>
          {busy ? <Saving /> : null}
        </div>
      </Dialog>
    );
  }

  const question = STEPS[step];
  const options = remaining(ranked, step);

  return (
    <Dialog label={question.title} onClose={dismiss}>
      <div className="exact-wizard" aria-busy={busy || undefined}>
        <p className="exact-wizard__step">
          Question {step + 1} of {STEPS.length}
        </p>
        <h2 className="exact-wizard__title" tabIndex={-1} ref={heading}>
          {question.title}
        </h2>
        <p className="exact-wizard__hint">{question.hint}</p>

        <ul className="exact-wizard__options">
          {options.map((factor) => (
            <li key={factor.key}>
              <button
                type="button"
                className="exact-wizard__option"
                onClick={() => choose(factor.key)}
                disabled={busy}
              >
                <span className="exact-wizard__option-label">{factor.label}</span>
                <span className="exact-wizard__option-hint">{factor.hint}</span>
              </button>
            </li>
          ))}
        </ul>

        {/* Back to the offer from the first question, so "I have changed my
            mind about answering at all" is reachable without leaving the
            dialog. Since #596 Escape no longer records anything either, so
            this is no longer the only non-recording way out — it is still the
            only one that leads back to the two answers rather than away from
            the question. */}
        <button
          type="button"
          className="exact-wizard__back"
          onClick={() => setStep(step === 0 ? null : step - 1)}
          disabled={busy}
        >
          Back
        </button>
        {busy ? <Saving /> : null}
      </div>
    </Dialog>
  );
}
