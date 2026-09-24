// Ranking the four suitability factors, and what that ranking is worth.
//
// CB asks this during signup, as steps 11-13 of a fourteen-step onboarding
// wizard nobody can decline: it reappears over every page until finished
// (`OnboardingWizard.tsx`, `UserProfile.is_onboarding`). The questions, the
// factor list and the arithmetic below are CB's, verbatim. What is not CB's
// is the offer: a reader here is asked once, on the trials page, and may say
// no. So the copy for the offer had to be written, and the copy for the three
// steps did not.
import { DEFAULT_WEIGHT, type WeightKey } from "./weights";
import type { FilterState } from "./types";

export interface Factor {
  key: WeightKey;
  /** CB's `label`. */
  label: string;
  /** CB's `subtitle` — what the word means, for a reader choosing between
   *  four abstractions. */
  hint: string;
}

/** CB's order, which is neither alphabetical nor by importance: it is the
 *  order the options are listed in on every step. Kept so a reader who has
 *  seen CB's wizard recognises this one. */
export const FACTORS: readonly Factor[] = [
  {
    key: "riskWeight",
    label: "Risk",
    hint: "Minimizing potential negative outcomes and side effects of treatment",
  },
  {
    key: "benefitWeight",
    label: "Benefit",
    hint: "Maximizing potential positive outcomes or effectiveness",
  },
  {
    key: "patientBurdenWeight",
    label: "Patient Burden",
    hint:
      "Minimizing inconvenience, discomfort, or effort required from " +
      "attending the trial",
  },
  {
    key: "distancePenaltyWeight",
    label: "Distance",
    hint: "Minimizing travel distance to the trial site",
  },
];

/** The three questions, CB's wording. */
export const STEPS: readonly { title: string; hint: string }[] = [
  {
    title: "What is your most important factor when choosing a trial?",
    hint:
      "Pick the most important factor to you. This helps us personalise how " +
      "trials are sorted for you.",
  },
  {
    title: "What is your second most important factor?",
    hint: "Choose from the remaining factors.",
  },
  {
    title: "What is your third most important factor?",
    hint: "The remaining factor will be ranked last.",
  },
];

/** What each place is worth. CB's `[40, 30, 20, 10]`.
 *
 *  The score divides the four by their sum, so only the ratios are read —
 *  these particular numbers are a presentation choice, and the one CB made.
 *  They also sum to 100, which is what makes them legible in the numeric form
 *  the same weights are editable in afterwards. */
export const PLACES: readonly number[] = [40, 30, 20, 10];

/** The weights a ranking implies.
 *
 *  Three answers, four factors: the fourth is whichever was not named, and it
 *  takes last place. CB derives it the same way — by difference, not by asking
 *  a fourth question that has only one possible answer.
 *
 *  Returns every key, not only the ranked ones. A partial answer would leave
 *  the unmentioned weights at whatever the reader had before, so a wizard run
 *  twice could blend two rankings into a third nobody chose. */
export const weightsFor = (ranked: readonly WeightKey[]): FilterState => {
  // Deduplicated first. The wizard cannot produce a repeat — it narrows the
  // list as it goes and drops later answers when an earlier one changes — but
  // this is an exported function whose contract above says "every key", and
  // on a repeat the untouched version quietly broke it: the second mention
  // overwrote the first, and the factor nobody had named fell off the end of
  // `PLACES` to `DEFAULT_WEIGHT`, which `weightsAreCustom` then reads as "no
  // opinion". A caller would have got three ranks and a shrug.
  const named = [...new Set(ranked)];
  const order = [
    ...named,
    ...FACTORS.map((f) => f.key).filter((key) => !named.includes(key)),
  ];
  const out: Record<string, number> = {};
  order.forEach((key, place) => {
    out[key] = PLACES[place] ?? DEFAULT_WEIGHT;
  });
  return out as FilterState;
};

/** The factors still on offer at `step`, given what has been picked.
 *
 *  CB narrows the list as it goes rather than letting a reader pick the same
 *  factor twice and correcting them afterwards. */
export const remaining = (ranked: readonly WeightKey[], step: number) =>
  FACTORS.filter((f) => !ranked.slice(0, step).includes(f.key));
