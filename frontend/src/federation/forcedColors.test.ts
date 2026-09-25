// The two controls that turn `forced-color-adjust` off, and who wins their
// colours.
//
// Windows High Contrast replaces every author colour with one from the user's
// palette. These two opt out, because the thing they need to say — "this
// segment is the chosen one", "this button commits the change" — is said in
// colour, and the replacement erases it. Opting out is right, and it is also a
// promise: it hands back EVERY author colour on the element, in every state,
// so the LAST word on each surface has to be the block's, or the control goes
// back to wearing the author's colours under a system text colour.
//
// That is how two bugs got in. `.exact-prefs__save:hover` is (0,3,0) against a
// (0,2,0) pin, so hovering repainted the button in the CTA's green while the
// label stayed `HighlightText` — 4.13:1 on the black `HighlightText` of High
// Contrast Black, and 2.71:1 after #561 darkened that green. And its focus
// ring stayed the author's blue.
//
// What is asserted is the CASCADE OUTCOME, not the presence of a declaration.
// An earlier version of this file checked that the block contained the right
// text, which every one of those bugs would also satisfy: appending
// `.exact-root .exact-prefs__save:hover { background: … }` to the end of the
// sheet restores the original regression in full and leaves such a check
// green. So each surface is resolved the way a browser resolves it — heaviest
// rule, then the last of equals — and the winner has to be inside the block.
//
// It is deliberately NOT a general guard. One was written for #561 and
// withdrawn: five of its versions passed while asserting nothing, and the
// version that finally asserted the right thing fired on correct CSS a
// legitimate edit would produce. Its design and its failures are in #575.
// This asks one question per surface per control, which is small enough to be
// obviously right and cannot object to how the CSS is spelled.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { blocksIn, branches, rulesIn, uncommented, weigh } from "../test/cssCascade";

const css = uncommented(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), "exact.css"), "utf8"),
);

/** The character ranges covered by forced-colors media blocks.
 *
 *  `not (forced-colors: active)` is excluded: it is the inverted sense, and
 *  reading it as the block would accept a pin that applies to everyone EXCEPT
 *  the readers it is for. */
const forcedRanges = (() => {
  const out: [number, number][] = [];
  const opener = /@media([^{]*)\{/g;
  for (let m = opener.exec(css); m; m = opener.exec(css)) {
    const conditions = m[1];
    if (!/\(\s*forced-colors\s*:\s*active\s*\)/.test(conditions)) continue;
    if (/\bnot\b/.test(conditions)) continue;
    let depth = 1;
    let i = m.index + m[0].length;
    for (; i < css.length && depth > 0; i += 1) {
      if (css[i] === "{") depth += 1;
      else if (css[i] === "}") depth -= 1;
    }
    out.push([m.index, i]);
    opener.lastIndex = i;
  }
  return out;
})();

const insideForcedColors = (at: number) => forcedRanges.some(([a, b]) => at > a && at < b);

/** Every rule, in source order, with where it sits.
 *
 *  One forward walk. Bodies repeat, so looking a rule up by its text
 *  afterwards collapses it onto the first copy — which reads a later rule as
 *  earlier, or as being inside the block when only its twin is. */
const rules = (() => {
  const out: { selector: string; body: string; at: number }[] = [];
  let cursor = 0;
  for (const [selector, body] of rulesIn(css)) {
    const at = css.indexOf(`{${body}}`, cursor);
    cursor = at + 1;
    out.push({ selector, body, at });
  }
  return out;
})();

type Writer = { selector: string; body: string; weight: number; at: number };

/** Every rule that writes `prop` on the element carrying `cls`, with where it
 *  actually sits.
 *
 *  Matched by CLASS, through `blocksIn`, rather than by a spelling of the
 *  selector — a rule reaching the element as `button.exact-prefs__save` or
 *  `:is(.exact-prefs__save)` counts exactly as much.
 *
 *  Paired with `rules` by a MONOTONIC WALK, because both are `rulesIn(css)` in
 *  source order and `blocksIn`'s result is a subsequence of it. The first
 *  version looked each rule's position up by its body text, which is wrong the
 *  moment two rules share a body — and one-declaration bodies share constantly.
 *  Measured on this sheet: `.exact-prefs__save:hover` sits at 40830 and was
 *  reported at 4203, the offset of an unrelated rule declaring the same single
 *  background. It passed only because that wrong number happened to fall on
 *  the right side of the pin, and the regression this file exists to catch —
 *  the same `:hover` appended at the end of the sheet — was handed a position
 *  nine thousand characters before its own and read as harmless. */
const writersOf = (cls: string, prop: RegExp): Writer[] => {
  const out: Writer[] = [];
  let cursor = 0;
  for (const [selector, body, weight] of blocksIn(css, cls)) {
    // Advance to this rule's own entry. Identity, not text.
    while (
      cursor < rules.length &&
      !(rules[cursor].selector === selector && rules[cursor].body === body)
    ) {
      cursor += 1;
    }
    expect(cursor, `no position for ${selector.trim()}`).toBeLessThan(rules.length);
    const at = rules[cursor].at;
    cursor += 1;
    if (!prop.test(body)) continue;
    out.push({ selector: selector.trim(), body, weight, at });
  }
  return out;
};

/** The rule a browser would let win: heaviest, then last of equals.
 *
 *  `!important` is NOT modelled, and that is a hole rather than a
 *  simplification — an earlier version of this comment had it the other way
 *  round. An `!important` on an author rule outside the block takes the
 *  surface without changing which rule this function picks, so the pin still
 *  reads as the winner and the test stays green while the browser paints the
 *  author's colour. Nothing on these two controls uses `!important`; the day
 *  something does, this needs to read importance before weight. */
const winnerOf = (writers: readonly Writer[]) =>
  writers.reduce((best, one) =>
    one.weight > best.weight || (one.weight === best.weight && one.at > best.at)
      ? one
      : best,
  );

/** The last word on `prop` for this control, and where it came from.
 *
 *  State-blind by default, which is the right question for a control whose
 *  states are all painted by the same pin. `where` narrows it to the rules
 *  that apply in ONE state, for a control where they are not: the wizard's
 *  primary button is `Highlight` while it can be pressed and the system's
 *  `GrayText` while a write is on the wire, and without the filter the
 *  disabled rule — later and heavier — would answer for the resting button
 *  too and the pin would read as missing. */
const decides = (
  cls: string,
  prop: RegExp,
  where: (selector: string) => boolean = () => true,
) => {
  const writers = writersOf(cls, prop).filter((w) => where(w.selector));
  expect(writers.length, `nothing writes ${prop} on ${cls}`).toBeGreaterThan(0);
  const won = winnerOf(writers);
  return {
    ...won,
    forced: insideForcedColors(won.at),
    value: (prop.exec(won.body) ?? [])[0] ?? "",
  };
};

const BACKGROUND = /background(-color)?\s*:\s*[^;}]+/;
const LABEL = /(^|[;\s])color\s*:\s*[^;}]+/;
const RING = /outline(-color)?\s*:\s*[^;}]+/;

describe("the preferences Save button in forced colors", () => {
  const CLS = ".exact-prefs__save";

  it("has the block deciding its fill", () => {
    // The regression, as the cascade sees it: the author's `:hover` is (0,3,0)
    // and the pin was (0,2,0), so the hover had the last word and the green
    // came back under `HighlightText`.
    const fill = decides(CLS, BACKGROUND);
    expect(fill.forced, `${fill.selector} has the last word on its fill`).toBe(true);
    expect(fill.value).toMatch(/Highlight\b/);
  });

  it("has the block deciding its label", () => {
    const label = decides(CLS, LABEL);
    expect(label.forced, `${label.selector} has the last word on its label`).toBe(true);
    expect(label.value).toMatch(/HighlightText\b/);
  });

  it("draws its focus ring in CanvasText, because the ring is OUTSET", () => {
    // `outline-offset: 2px` puts the ring outside the border box, on the
    // dialog's `Canvas`. `HighlightText` is the colour guaranteed against
    // `Highlight` — which the ring is not on — and in both palettes Windows
    // ships it IS the canvas colour: 1.00:1, a ring that is not there. That
    // shipped briefly, from copying the segmented control's answer without its
    // geometry, so both halves are asserted here.
    expect(css).toMatch(
      /\.exact-root \.exact-prefs__save:focus-visible \{[^}]*outline-offset:\s*2px/,
    );
    const ring = decides(CLS, RING);
    expect(ring.forced, `${ring.selector} has the last word on its ring`).toBe(true);
    expect(ring.value).toMatch(/CanvasText\b/);
    expect(ring.value).not.toMatch(/HighlightText\b/);
  });
});

describe("the weights wizard's primary button in forced colors", () => {
  const CLS = ".exact-wizard__go";
  /** The button as a reader can press it. Its disabled rule is a different
   *  question, asked below. */
  const pressable = (selector: string) => !selector.includes(":disabled");

  it("has the block deciding its fill", () => {
    // Without this the offer's two buttons are the same ButtonFace, and one
    // of them declines permanently.
    const fill = decides(CLS, BACKGROUND, pressable);
    expect(fill.forced, `${fill.selector} has the last word on its fill`).toBe(true);
    expect(fill.value).toMatch(/Highlight\b/);
  });

  it("has the block deciding its label", () => {
    const label = decides(CLS, LABEL, pressable);
    expect(label.forced, `${label.selector} has the last word on its label`).toBe(true);
    expect(label.value).toMatch(/HighlightText\b/);
  });

  it("hands the disabled state back to the system, in the system's own words", () => {
    // `forced-color-adjust: none` keeps the pin's colours through `:disabled`
    // too, so without a rule of its own the button sits at `Highlight` under
    // `opacity: 0.5` for the whole of a write — half-transparent
    // chosen-thing, which is not how this mode says "disabled". `GrayText`
    // is.
    const disabled = (selector: string) => selector.includes(":disabled");
    const fill = decides(CLS, BACKGROUND, disabled);
    expect(fill.forced, `${fill.selector} has the last word on its fill`).toBe(true);
    // The value too: `background: Highlight; color: GrayText` would satisfy a
    // check that only asks who won, and GrayText on Highlight is worse than
    // what it replaced.
    expect(fill.value).toMatch(/ButtonFace\b/);
    // And at full strength — asked as "who wins", not "is it written". The
    // first version matched the text of `opacity: 1` inside this block while
    // the plain `:disabled` group below it, same weight and later in source,
    // actually decided: the system pair was still drawn at half alpha and the
    // pin read as present.
    const fade = decides(CLS, /opacity\s*:\s*[^;}]+/, disabled);
    expect(fade.forced, `${fade.selector} has the last word on its opacity`).toBe(
      true,
    );
    expect(fade.value).toMatch(/opacity:\s*1\b/);
    const label = decides(CLS, LABEL, disabled);
    expect(label.forced, `${label.selector} has the last word on its label`).toBe(true);
    expect(label.value).toMatch(/GrayText\b/);
  });

  it("draws its focus ring in CanvasText, because the ring is OUTSET", () => {
    // Same geometry as the Save button, so the same answer — and the same
    // reason HighlightText would be wrong: at `outline-offset: 2px` the ring
    // is on Canvas, not on the fill.
    expect(css).toMatch(
      /\.exact-root \.exact-wizard__go:focus-visible[^{]*\{[^}]*outline-offset:\s*2px/,
    );
    const ring = decides(CLS, RING);
    expect(ring.forced, `${ring.selector} has the last word on its ring`).toBe(true);
    expect(ring.value).toMatch(/CanvasText\b/);
    expect(ring.value).not.toMatch(/HighlightText\b/);
  });

  it("leaves the decline to the system, so the two are still different", () => {
    // Pinning both would put them back where they started. This asserts the
    // absence deliberately: `Keep them equal` must NOT opt out.
    const fill = decides(".exact-wizard__skip", BACKGROUND);
    expect(fill.forced, "the decline is repainted by the system").toBe(false);
  });
});

describe("the chosen segment in forced colors", () => {
  const CLS = ".exact-seg__btn";

  it("has the block deciding its fill", () => {
    // Here the pin and the author's `:hover` are both (0,3,0), so the pin wins
    // on source order alone. Moving that hover below the media block — tidying,
    // grouping the media queries — would hand the fill back with nothing else
    // changed, which is why this asks who wins rather than who is present.
    const fill = decides(CLS, BACKGROUND);
    expect(fill.forced, `${fill.selector} has the last word on its fill`).toBe(true);
    expect(fill.value).toMatch(/Highlight\b/);
  });

  it("has the block deciding its label", () => {
    const label = decides(CLS, LABEL);
    expect(label.forced, `${label.selector} has the last word on its label`).toBe(true);
    expect(label.value).toMatch(/HighlightText\b/);
  });

  it("draws its focus ring in HighlightText, because the ring is INSET", () => {
    // The mirror image, and the reason the two controls get different answers.
    // This ring is `outline-offset: -2px` — the button fills its segment edge
    // to edge, so an outset ring would be clipped — which puts it ON the
    // `Highlight` fill, where `HighlightText` is the right pair.
    expect(css).toMatch(
      /\.exact-root \.exact-seg__btn:focus-visible \{[^}]*outline-offset:\s*-2px/,
    );
    const ring = decides(CLS, RING);
    expect(ring.forced, `${ring.selector} has the last word on its ring`).toBe(true);
    expect(ring.value).toMatch(/HighlightText\b/);
  });
});

describe("the headings the wizard moves focus to", () => {
  // Not a forced-colors question, but the same machinery answers it: who has
  // the last word on `outline` for an element that is a focus DESTINATION
  // rather than a control.
  // Longhands too. `outline: none` sets `outline-style: none`, so a later
  // rule restoring the ring as `outline-style: solid` slips past a pattern
  // that only knows the shorthand — demonstrated, and green.
  const OUTLINE = /outline(-color|-style|-width|-offset)?\s*:\s*[^;}]+/;

  for (const cls of [".exact-wizard__title", ".exact-list__title"]) {
    it(`draws no ring on ${cls}, which no one can tab to`, () => {
      // `tabindex="-1"`: the wizard opens by itself and moves focus to the
      // question, and on the way out hands it to the list heading. With no
      // input preceding it the browser reads that as keyboard focus and
      // paints its default ring — measured on the stand as
      // `outline: rgb(0, 95, 204) auto 1px` around the heading, and absent
      // once a click has happened, which is the tell. A keyboard user loses
      // nothing: they cannot land here by tabbing.
      // No `where` filter, deliberately. `decides` takes one to narrow a pin
      // to a single STATE, which is the right question for a control whose
      // states are painted by different rules. It is the wrong question
      // here, and wrong in the dangerous direction: a state-blind rule —
      // `outline` with no `:focus` in its selector — applies while focused
      // too, and a filter looking for `:focus` drops it before the cascade
      // is weighed. Demonstrated: a later `.exact-root .exact-list
      // .exact-list__title { outline: 3px solid red }` at the same weight
      // wins in a browser and left this green. Any writer of an outline on
      // this element is a candidate.
      const ring = decides(cls, OUTLINE);
      expect(ring.value).toMatch(/outline:\s*none\b/);
    });
  }
});

describe("the list itself", () => {
  it("names every control that opts out", () => {
    // The one thing a list cannot do is notice a new member, so it says when
    // it has stopped being complete.
    //
    // Counted as CONTROLS, not as declarations. An earlier version counted
    // `forced-color-adjust: none` occurrences, which a third control added to
    // an existing selector list leaves unchanged — the obvious way to add one,
    // defeating the only check that would have caught it — and which splitting
    // one pin into two rules changes without anything being wrong.
    const opted = new Set<string>();
    for (const rule of rules) {
      if (!/forced-color-adjust\s*:\s*none/.test(rule.body)) continue;
      if (!insideForcedColors(rule.at)) continue;
      for (const branch of branches(rule.selector)) {
        const compound = branch.split(/[\s>+~]+/).filter(Boolean).pop() ?? "";
        for (const m of compound.matchAll(/\.[\w-]+/g)) {
          // `.is-on` is a state, not a control — the opt-out is on the
          // segmented button in one of its states, and listing the state
          // separately would make the set change whenever the state does.
          if (m[0] !== ".is-on") opted.add(m[0]);
        }
      }
    }
    expect(
      [...opted].sort(),
      "a control turned forced-colors adjustment off without being covered by " +
        "this file — add it, or see #575 for the general check",
    ).toEqual([".exact-prefs__save", ".exact-seg__btn", ".exact-wizard__go"]);
  });

  it("weighs a rule the way the cascade does", () => {
    // `winnerOf` is the only inference here, so it is checked rather than
    // assumed: heavier wins, and equals are settled by whichever comes last.
    const a = { selector: ".a", body: "", weight: weigh(".a"), at: 10 };
    const b = { selector: ".a .b", body: "", weight: weigh(".a .b"), at: 0 };
    const c = { selector: ".c", body: "", weight: weigh(".c"), at: 20 };
    expect(winnerOf([a, b]).selector).toBe(".a .b");
    expect(winnerOf([a, c]).selector).toBe(".c");
    expect(winnerOf([c, a]).selector).toBe(".c");
  });
});
