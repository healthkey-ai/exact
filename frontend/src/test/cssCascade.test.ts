// The cascade model, against sheets written to break it.
//
// Everything in `controlsRowCss.test.ts` rests on these three functions, and
// a wrong answer here is invisible: the assertions stay green and simply stop
// covering what they name. So the model is tested on its own, with sheets
// whose right answer is known by reading the cascade rules rather than by
// running this code.

import { describe, expect, it } from "vitest";

import { blocksIn, branches, importantAt, rulesIn, uncommented, weigh } from "./cssCascade";

describe("uncommented", () => {
  it("drops a declaration that is only inside a comment", () => {
    expect(uncommented("a { /* color: red; */ }")).not.toMatch(/color/);
  });

  it("does not take a comment opener inside a string for one", () => {
    // `content: "/*"` swallowed the rest of the sheet to the next `*/`,
    // hiding every override in between — the failure this scanner exists for.
    const css = 'a { content: "/*"; } b { color: red; }';
    expect(uncommented(css)).toMatch(/b \{ color: red; \}/);
  });

  it("does not take one inside url() for one either", () => {
    const css = "a { background: url(/img/a/*.svg); } b { color: red; }";
    expect(uncommented(css)).toMatch(/b \{ color: red; \}/);
  });
});

describe("weigh", () => {
  it("orders id over class over element, the way the cascade does", () => {
    expect(weigh("#a")).toBeGreaterThan(weigh(".a.b.c.d.e"));
    expect(weigh(".a")).toBeGreaterThan(weigh("div span p"));
  });

  it("counts a repeated class twice — the doubling trick this sheet uses", () => {
    expect(weigh(".exact-root.exact-root .x")).toBeGreaterThan(
      weigh(".exact-root .x"),
    );
  });

  it("counts attributes and pseudo-classes as classes, not elements", () => {
    expect(weigh('a[href]')).toBe(weigh("a.x"));
    expect(weigh("a:hover")).toBe(weigh("a.x"));
  });
});

describe("blocksIn", () => {
  const css = `
    .seg { flex: 1 1 0; }
    .seg span { color: red; }
    .wrap .seg { flex: 1 1 auto; }
  `;

  it("finds the blocks that style the element itself", () => {
    expect(blocksIn(css, ".seg").map(([s]) => s)).toEqual([
      ".seg",
      ".wrap .seg",
    ]);
  });

  it("leaves out a rule that styles the element's CHILDREN", () => {
    // `.seg span` is about the span. Counted as a rule on `.seg`, a
    // `display:` in it would read as overriding the group's own.
    expect(blocksIn(css, ".seg").map(([s]) => s)).not.toContain(".seg span");
  });

  it("weighs the branch that matched, not the heaviest branch in the list", () => {
    // `#unrelated` is not this element. Taking the list's maximum, this rule
    // reads as id-weight, and a later rule that really does override it is
    // dismissed as lighter — the assertion passing over the regression.
    const list = ".seg, #unrelated { flex: 1 1 0; }";
    const [[, , pushes]] = blocksIn(list, ".seg");
    expect(pushes).toBe(weigh(".seg"));
    expect(pushes).toBeLessThan(weigh("#unrelated"));
  });

  it("takes the heaviest branch that DID match, when several do", () => {
    const list = ".seg, .wrap .seg.seg { flex: 1 1 0; }";
    const [[, , pushes]] = blocksIn(list, ".seg");
    expect(pushes).toBe(weigh(".wrap .seg.seg"));
  });

  it("reads rules inside at-rules, where an override still overrides", () => {
    const css2 = "@container (width < 30rem) { .seg { flex: 0 0 auto; } }";
    expect(blocksIn(css2, ".seg")).toHaveLength(1);
  });

  it("does not match a class that merely starts with the name", () => {
    expect(blocksIn(".seg-icon { color: red; }", ".seg")).toHaveLength(0);
  });
});

describe("rulesIn", () => {
  it("pairs each selector with its own body", () => {
    expect(rulesIn("a { x: 1; } b { y: 2; }")).toEqual([
      ["a", " x: 1; "],
      ["b", " y: 2; "],
    ]);
  });
});

describe("weigh, on the functional pseudo-classes", () => {
  // These weigh as their ARGUMENT, not as themselves. Counting the wrapper
  // as a class and its argument as another inflates every selector using
  // one — this sheet's own `.exact-seg__item:not(:last-child)` read as three
  // classes where the cascade sees two — and an inflated declaration
  // dismisses a real equal-specificity override as lighter.

  it("drops the :not() wrapper and keeps its argument", () => {
    expect(weigh(".a:not(.b)")).toBe(weigh(".a.b"));
    expect(weigh(".exact-seg__item:not(:last-child)")).toBe(weigh(".a.b"));
  });

  it("takes the most specific branch inside :is()", () => {
    expect(weigh(":is(.a, #b)")).toBe(weigh("#b"));
    expect(weigh(":is(div, .a)")).toBe(weigh(".a"));
  });

  it("gives :where() no weight at all, argument included", () => {
    expect(weigh(".a:where(#b)")).toBe(weigh(".a"));
  });

  it("unwinds nesting from the inside out", () => {
    expect(weigh(".a:not(:is(.b, div))")).toBe(weigh(".a.b"));
  });

  it("still counts a pseudo-ELEMENT, which is element-weight", () => {
    expect(weigh(".a::before")).toBeGreaterThan(weigh(".a"));
    expect(weigh(".a::before")).toBeLessThan(weigh(".a.b"));
  });
});

describe("branches", () => {
  // A naive `split(",")` tears a functional pseudo-class in half, and the
  // half carrying the id disappears — which is how a selector that really
  // does win gets read as a tie.
  it("splits only on the commas between branches", () => {
    expect(branches(".a, .b")).toEqual([".a", ".b"]);
    expect(branches(":is(#nope, .root) .x")).toEqual([":is(#nope, .root) .x"]);
    expect(branches('[title="a, b"], .c')).toEqual(['[title="a, b"]', ".c"]);
    // A bracket inside a STRING is text, and only the quote tracking sees
    // that: on depth alone this closes the attribute early and the comma
    // reads as a separator.
    expect(branches('[title="a]b"], .c')).toEqual(['[title="a]b"]', ".c"]);
  });
});

describe("weigh, the cases a regex gets wrong", () => {
  it("unwraps a functional pseudo-class that CONTAINS parentheses", () => {
    // `:not(:nth-child(2))` weighs as `:nth-child(2)` — one class. Left
    // wrapped, the wrapper counted too and the declaration read heavier than
    // it is, which is the direction that produces false greens.
    expect(weigh(".seg:not(:nth-child(2))")).toBe(weigh(".a.b"));
    // And the case that tells a matching-paren scan from a lazy one: with
    // `indexOf(")")` the argument list is cut at the INNER close paren, the
    // id branch is never seen, and this weighs as a class.
    expect(weigh(":is(:nth-child(2), #z)")).toBe(weigh("#z"));
  });

  it("does not read an attribute VALUE as selector", () => {
    // `#` and whitespace inside a quoted value are text. Counted, they add
    // an id and an element that the cascade does not see — weighing the
    // declaration above its real specificity, which is how a genuine
    // override gets dismissed as lighter.
    expect(weigh('a[href="#top"]')).toBe(weigh("a.q"));
    expect(weigh('a[title="x y"]')).toBe(weigh("a.q"));
    expect(weigh('a[data-x="a.b"]')).toBe(weigh("a.q"));
  });

  it("takes the id inside :is() even when a lighter branch comes first", () => {
    expect(weigh(":is(#nope, .root) .x")).toBeGreaterThan(weigh(".root .x"));
  });

  it("weighs the legacy one-colon pseudo-elements as elements", () => {
    // `:before` is a pseudo-ELEMENT in its old spelling: (0,1,1), not (0,2,0).
    expect(weigh(".a:before")).toBe(weigh(".a::before"));
    expect(weigh(".a:before")).toBeLessThan(weigh(".a.b"));
  });
});

describe("importantAt", () => {
  it("finds it on the declaration the pattern matched", () => {
    expect(importantAt("flex: 1 1 0 !important;", /flex\s*:/)).toBe(true);
    expect(importantAt("flex: 1 1 0;", /flex\s*:/)).toBe(false);
  });

  it("does not borrow it from a LATER, unrelated declaration", () => {
    expect(importantAt("flex: 1 1 0; color: red !important;", /flex\s*:/)).toBe(false);
  });

  it("finds it on a SECOND declaration of the same property", () => {
    // The cascade takes the last one in the block, and it is the one that
    // shouts. Reading only the first reports a block that reverts a fix as
    // harmless.
    expect(importantAt("flex: 0 1 auto; flex: 1 1 0 !important;", /flex\s*:/)).toBe(true);
  });

  it("is false when the pattern finds nothing", () => {
    expect(importantAt("color: red !important;", /flex\s*:/)).toBe(false);
  });
});

describe("blocksIn, on the element's other names", () => {
  // One element, several classes: a rule hung on any of them styles it.
  const css = `
    .item { flex: 1 1 auto; }
    .tip { flex: 1 1 0; }
  `;

  it("finds rules reaching the element by either class", () => {
    expect(blocksIn(css, ".item", ".tip").map(([sel]) => sel)).toEqual([
      ".item",
      ".tip",
    ]);
  });

  it("reads the last compound past a functional pseudo-class that holds a space", () => {
    // `:not(:nth-child(1), :nth-child(2))` and `:is(.item, .x)` both carry a
    // space, and splitting the branch on whitespace leaves that fragment as
    // the "last compound" — so the rule reads as styling something else and
    // the block goes unseen.
    const tricky = `
      .item:not(:nth-child(1), :nth-child(2)) { flex: 1 1 0; }
      .item:is(.item, .x) { flex: 1 1 0; }
      [data-x="a b"].item { flex: 1 1 0; }
    `;
    expect(blocksIn(tricky, ".item")).toHaveLength(3);
  });

  it("sees the class when it is inside :is(), where the rule reaches it", () => {
    // `:is(#unused, .item)` styles the element, with ID weight. Strip the
    // argument and the rule becomes invisible while overriding everything.
    const css = ".root :is(#unused, .item) { flex: 1 1 0; }";
    const found = blocksIn(css, ".item");
    expect(found).toHaveLength(1);
    expect(found[0][2]).toBeGreaterThan(weigh(".root .item"));
  });

  it("does not claim a rule that says the element is NOT that class", () => {
    // `:not(.item)` and `:has(.item)` name something the subject is not, or
    // something beneath it.
    expect(blocksIn(".foo:not(.item) { flex: 1 1 0; }", ".item")).toHaveLength(0);
    expect(blocksIn(".foo:has(.item) { flex: 1 1 0; }", ".item")).toHaveLength(0);
  });
});
