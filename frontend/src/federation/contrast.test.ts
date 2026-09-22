// What the shipped palette measures, in the pairs the page actually paints.
//
// A token is not readable or unreadable on its own — only against what it is
// drawn on. #553 was exactly that: `success-700` looks like a dark green, and
// on the fill it is paired with it was 4.01:1, while the amber and red tiers
// beside it passed. Nothing said so, because nothing here multiplied the two
// together.
//
// The pairs are written out rather than derived from the stylesheet. Deriving
// them needs a layout engine — which element ends up on which background is a
// question about the DOM, not about the sheet — and a test that guessed would
// be measuring its own guess. The list is short and the comments say where
// each pair is painted; a new pair is a line here.

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { uncommented } from "../test/cssCascade";

// Comments out first. This file's own declarations are introduced by
// comments that quote colours and ratios, and a regex reading the raw sheet
// takes `/* until #553: --exact-color-success-700: hsl(152 91% 26%); */` for
// a declaration — and reading THAT above a real one that had gone back to
// 34% turned every assertion here green over a 2.98:1 palette. The direction
// matters: it is a comment holding the GOOD value that hides a bad one.
const css = uncommented(readFileSync(new URL("./exact.css", import.meta.url), "utf8"));

/** A token's value, following a `var()` to the token it names.
 *
 *  Throws plainly rather than through `expect`, because some of these are
 *  resolved inside an `it.fails`, and `it.fails` passes on ANY throw — a
 *  renamed or deleted token would "successfully fail" and the suite would
 *  stay green over a page whose mark resolves to nothing. Measured: that is
 *  what happened before the values below were hoisted out of those bodies. */
const token = (name: string, seen: readonly string[] = []): string => {
  if (seen.includes(name)) throw new Error(`${name} is declared as a cycle`);
  // The LAST declaration, because that is the one the cascade takes. First
  // wins in a regex and last wins in CSS, and the difference is silent: a
  // second block of tokens — a dark mode, a forced-colors override — would
  // detach every ratio here from the page.
  const all = [...css.matchAll(new RegExp(`${name}:\\s*([^;]+);`, "g"))];
  if (all.length === 0) throw new Error(`${name} is not declared`);
  const value = all.at(-1)![1].trim();
  const indirect = /^var\((--[\w-]+)\)$/.exec(value);
  return indirect ? token(indirect[1], [...seen, name]) : value;
};

/** `hsl(h s% l%)` or `#rrggbb` → [r, g, b]. */
const rgb = (value: string): [number, number, number] => {
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const hsl = /^hsl\(\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\s*\)$/.exec(value);
  if (!hsl) throw new Error(`cannot read ${value}`);
  const [h, s, l] = [Number(hsl[1]), Number(hsl[2]) / 100, Number(hsl[3]) / 100];
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] = [
    [c, x, 0],
    [x, c, 0],
    [0, c, x],
    [0, x, c],
    [x, 0, c],
    [c, 0, x],
  ][Math.floor(h / 60) % 6];
  // Rounded to 8 bits, which is what a browser rasterises and therefore what
  // the reader's eye is given.
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)];
};

const luminance = (value: string) => {
  const [r, g, b] = rgb(value).map((channel) => {
    const c = channel / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

/** WCAG 2.x contrast, full precision.
 *
 *  Not rounded before the comparison, however much nicer 4.50 reads than
 *  4.4962: rounding to two decimals lets everything from 4.495 up answer
 *  "4.50" and pass a 4.5 threshold it does not meet. The rounding belongs in
 *  the message, where a human reads it. */
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

/** `4.4962:1` — for the failure message, not for the comparison. */
const shown = (ratio: number) => `${ratio.toFixed(4)}:1`;

/** AA for text that is neither 18.66px bold nor 24px. Everything measured
 *  here is 11-14px. */
const AA = 4.5;
/** AA for a graphical mark that carries meaning on its own. */
const MARK = 3;

describe("the palette, in the pairs the page paints", () => {
  it("reads the tiers as text on their own fills", () => {
    // `.exact-elig__cell.is-*`, and `TIER_TOKENS` in `bits.tsx` — the score
    // pill draws the same three ramps.
    const tiers = [
      ["success", "the matched criterion cell and the green score pill"],
      ["warning", "the amber score pill"],
      ["error", "the mismatched cell and the red pill"],
    ] as const;
    for (const [ramp, where] of tiers) {
      const measured = contrast(
        token(`--exact-color-${ramp}-700`),
        token(`--exact-color-${ramp}-50`),
      );
      expect(measured, `${ramp}-700 on ${ramp}-50 (${shown(measured)}) — ${where}`)
        .toBeGreaterThanOrEqual(AA);
    }
  });

  it("reads the same tier text on the card behind it", () => {
    // `.exact-mcl__verdict.is-*` sits on the panel rather than on a tinted
    // cell. (`.exact-elig__check` does NOT — it is inside the matched cell,
    // and is covered by the pair above.) Lightening a fill can never repair
    // a ratio this one fails: the card is the lightest thing a fill can be.
    for (const ramp of ["success", "warning", "error"] as const) {
      const measured = contrast(token(`--exact-color-${ramp}-700`), token("--exact-color-surface"));
      expect(measured, `${ramp}-700 on the card (${shown(measured)})`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("reads the verdict marks that carry their meaning as colour", () => {
    // `.exact-mcl__item .exact-mcl__mark` — states of one cell, read by hue.
    // A mark is held to 3:1, not 4.5:1. `potential` is the third and it is
    // below even that; see the tracked failure below.
    for (const name of ["eligible", "not-eligible"] as const) {
      const measured = contrast(token(`--exact-color-${name}`), token("--exact-color-surface"));
      expect(measured, `${name} mark on the card (${shown(measured)})`).toBeGreaterThanOrEqual(MARK);
    }
  });

  // Resolved here, not inside the `it.fails` below: see `token`.
  const band = {
    eligible: contrast(token("--exact-color-eligible"), token("--exact-color-surface")),
    potential: contrast(token("--exact-color-potential"), token("--exact-color-surface")),
    notEligible: contrast(token("--exact-color-not-eligible"), token("--exact-color-surface")),
  };

  // `STATUS_COLOR` in `TrialsGraph.tsx` is not only the colour of a node: the
  // same three tokens are the `fill` of the band LABELS — "Met", "Not known",
  // "Not met" — drawn at 11px and 600 weight. That is text, so the bar is 4.5
  // and not the 3 a mark answers to. One test each, so that fixing one of
  // them is not swallowed by another still failing in the same loop.
  it.fails("reads Met as a band label — #562", () => {
    expect(band.eligible, `eligible (${shown(band.eligible)})`).toBeGreaterThanOrEqual(AA);
  });

  it.fails("reads Not known as a band label — #562", () => {
    expect(band.potential, `potential (${shown(band.potential)})`).toBeGreaterThanOrEqual(AA);
  });

  it("reads Not met as a band label", () => {
    expect(band.notEligible, `not-eligible (${shown(band.notEligible)})`).toBeGreaterThanOrEqual(AA);
  });

  const amberMark = contrast(token("--exact-color-potential"), token("--exact-color-surface"));

  it.fails("reads the amber verdict mark — #562", () => {
    // 2.15:1 on the card, below the 3:1 a meaning-carrying mark is held to,
    // and the palette already holds a readable amber: `warning-700` is
    // 5.05:1 and is what the verdict LINE beside this mark uses. Pointing
    // one at the other is a one-line change and a change to what CancerBot
    // shows, which is why it is tracked rather than taken here.
    expect(amberMark, `potential mark on the card (${shown(amberMark)})`)
      .toBeGreaterThanOrEqual(MARK);
  });

  it("reads the text ramp on each surface it is actually drawn on", () => {
    // Pairs, not a cross-product: `text-tertiary` never meets `surface-2`
    // (the chips that use that fill draw `text` and `text-muted`), and a
    // test asserting it would be measuring a combination the page does not
    // make. What `text-tertiary` DOES meet is the tinted cell fills, which
    // is where it was failing — the units sit inside `.exact-elig__cell`, as
    // does the stacked Required / Your Value header below 679px. NOT the
    // "not a requirement" note, which reads as the obvious third case and is
    // not one: it renders only for a field the trial does not evaluate, and
    // a cell is tinted only when it matched.
    const painted: ReadonlyArray<readonly [string, string, string]> = [
      ["text", "surface", "body copy on the card"],
      ["text", "surface-2", "the current page marker"],
      ["text-muted", "surface", "secondary copy"],
      ["text-muted", "surface-2", "the tab count"],
      ["text-tertiary", "surface", "units and headers outside a tinted cell"],
      ["text-tertiary", "success-50", "the same, inside a matched cell"],
      ["text-tertiary", "error-50", "…and inside a mismatched one"],
      // The one red-text-on-green-fill pair in the sheet: the register
      // card's "Couldn't save that", drawn in the verdict red on the card's
      // own success fill. It clears AA by four hundredths, which is the
      // kind of margin worth having a test hold still.
      ["not-eligible", "success-50", "the register card's save error"],
      ["not-eligible", "warning-50", "…the same card in its warning state"],
    ];
    for (const [name, on, where] of painted) {
      const measured = contrast(token(`--exact-color-${name}`), token(`--exact-color-${on}`));
      expect(measured, `${name} on ${on} (${shown(measured)}) — ${where}`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("reads the label on a chosen segment and a chosen tab count", () => {
    // `.exact-seg__btn.is-on` and `.exact-tab__count` — `primary` text on
    // the `primary-50` tint.
    expect(
      contrast(token("--exact-color-primary"), token("--exact-color-primary-50")),
      "primary on primary-50",
    ).toBeGreaterThanOrEqual(AA);
  });

  it("reads the label on the bookmark when it is on", () => {
    expect(
      contrast(token("--exact-color-on-primary"), token("--exact-color-primary")),
      "on-primary on primary",
    ).toBeGreaterThanOrEqual(AA);
  });

  const cta = contrast(token("--exact-color-on-brand"), token("--exact-color-brand-green"));

  it.fails("reads the CTA label on its own fill — #561", () => {
    // Known and tracked, not accepted: white on `--exact-color-brand-green`
    // is 2.31:1, which is below AA and below even the 3:1 a mark is held to.
    // It is the "View Trial" and "Register interest" buttons, so it is the
    // most prominent thing on the page and the worst pair in the palette.
    //
    // Left failing rather than fixed here because the fix is CancerBot's
    // brand green itself, which is a decision about the product's signature
    // colour and not one to take inside a contrast patch (#553 moved a ramp
    // step; this moves a brand). `it.fails` so the day it is fixed, this
    // test says so instead of going quietly green.
    expect(cta, `on-brand on brand-green (${shown(cta)})`).toBeGreaterThanOrEqual(AA);
  });

  it("reads the CTA label on the hover fill", () => {
    // The hover half of the same button, which `success-700` carries.
    expect(
      contrast(token("--exact-color-on-brand"), token("--exact-color-brand-green-hover")),
      "on-brand on brand-green-hover",
    ).toBeGreaterThanOrEqual(AA);
  });
});
