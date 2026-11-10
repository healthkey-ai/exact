/** The controls row's CSS, read as text.
 *
 *  Layout that depends on width cannot be tested by rendering: jsdom lays
 *  nothing out, so a container query that matches nothing looks exactly like
 *  one that matches. These two assertions are about the sheet itself, and
 *  they cover the two ways this row goes quietly wrong.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { blocksIn, importantAt, rulesIn, uncommented } from "../test/cssCascade";

describe("the controls row", () => {
  const raw = readFileSync(new URL("./exact.css", import.meta.url), "utf8");
  const css = uncommented(raw);
  const blocksFor = (...names: readonly string[]) => blocksIn(css, ...names);
  /** The segment slot, by both the classes it carries. */
  const SEGMENT = [".exact-seg__item", ".exact-action-tip"] as const;

  /** A declaration that is made, and that nothing else takes back.
   *
   *  Not merely "no later block": the cascade weighs specificity FIRST and
   *  only then source order, so a heavier selector ANYWHERE in the sheet
   *  wins — including above. Modelled on order alone, this helper passed a
   *  rule inserted above the fix that fully reverted it. */
  const stands = (
    blocks: readonly (readonly [string, string, number])[],
    declared: RegExp,
    undone: RegExp,
  ) => {
    const at = blocks.findIndex(([, body]) => declared.test(body));
    expect(at, `nothing declares ${declared}`).toBeGreaterThanOrEqual(0);
    // The winning block is read for what comes AFTER the declaration too. A
    // block that says the property twice takes the cascade with its second
    // copy, and `flex: 1 1 auto; flex: 1 1 0;` in one block — the shape a
    // careless merge leaves — reverted the fix with every assertion green.
    const own = blocks[at][1];
    const mineAt = own.search(declared);
    expect(own.slice(mineAt + 1), "the declaring block takes it back").not.toMatch(undone);
    const mine = blocks[at][2];
    const mineShouts = importantAt(own, declared);
    blocks.forEach(([, body, pushes], index) => {
      if (index === at) return;
      // `!important` is decided BEFORE specificity and order, so one of them
      // anywhere in the sheet takes the declaration back — including from a
      // lighter selector above it. Measured: `flex: 1 1 0 !important` added
      // to the earlier, lighter `.exact-root .exact-seg__item` block fully
      // reverted the fix in a browser while every assertion here stayed
      // green.
      const shouts = importantAt(body, undone);
      const wins = mineShouts
        ? shouts && (pushes > mine || (index > at && pushes === mine))
        : shouts || pushes > mine || (index > at && pushes === mine);
      if (wins) expect(body).not.toMatch(undone);
    });
  };

  it("never spells the undoing of either fix, in any rule", () => {
    // What `blocksFor` cannot see: a rule reaching these elements by SHAPE
    // rather than by class — `.exact-list > nav`, `nav[aria-label="…"]`,
    // `.exact-list__controls > div:last-child` — each of which puts the
    // phone overflow back with every assertion below still green. Resolving
    // a selector against the DOM is beyond a test that reads the sheet as
    // text; refusing the three declarations that do the undoing is not.
    // None of them is in this sheet today. If one is ever needed, this test
    // is where that conversation starts.
    //
    // It raises the bar rather than closing the door, and the difference is
    // worth knowing: `contain: content`, `contain: layout paint` and
    // `flex-wrap: initial` say the same things in other words, and a rule
    // reaching these elements by shape can still use them. Named below, so
    // the list is at least the ones somebody would plausibly write.
    expect(css).not.toMatch(/contain\s*:\s*none/);
    expect(css).not.toMatch(/flex-wrap\s*:\s*nowrap/);
    // `overflow-x` only: the `overflow: visible` shorthand is in this sheet
    // already, on a zero-height slot that is meant to spill (the pill over
    // the list), and banning it outright would be a test about something
    // else. The axis spelled out is the one that undoes the strip.
    expect(css).not.toMatch(/overflow-x\s*:\s*visible/);
    // `contain` without `size` or `inline-size` is the strip's containment
    // gone, spelled as an addition.
    // `initial`, `revert` and `unset` too: for `contain` the initial value
    // IS `none`, and for `flex-wrap` it is `nowrap` — the same undoing,
    // spelled as a word that does not name it.
    expect(css).not.toMatch(
      /contain\s*:\s*(?:content|layout|paint|style|initial|revert(-layer)?|unset)\b/,
    );
    expect(css).not.toMatch(/flex-wrap\s*:\s*(?:initial|revert(-layer)?|unset)\b/);
  });

  it("declares its query container off the element the host mounts", () => {
    // `inline-size` containment computes an element's intrinsic width as if
    // it had no contents. On `.exact-list` — the element a host mounts —
    // that collapses the whole list to its padding in any host that sizes
    // the remote by content (a flex row, an `inline-block`, an auto grid
    // track). The controls row takes its width from the list either way.
    expect(css).toMatch(
      /\.exact-root \.exact-list__controls\s*\{[^}]*container-type:\s*inline-size/,
    );
    expect(css).not.toMatch(/\.exact-root\.?\s?\.?exact-list\s*\{[^}]*container-type/);
  });

  it("names the container every query asks for", () => {
    // A query naming a container that does not exist is not an error: it
    // simply never matches, and the layout silently stays on its fallback.
    const declared = [...css.matchAll(/container-name:\s*([\w-]+)/g)].map((m) => m[1]);
    const asked = [...css.matchAll(/@container\s+([\w-]+)/g)].map((m) => m[1]);
    expect(asked.length).toBeGreaterThan(0);
    for (const name of asked) expect(declared).toContain(name);
  });

  it("keeps the tab strip a block that fills its row", () => {
    // The strip's bottom rule is what the active tab's underline sits on, so
    // it has to span the row rather than stop at the last tab. That holds
    // while the strip is a BLOCK in the list — `inline-flex`, or a flex
    // parent putting something beside it, shrinks it to its contents and the
    // rule with it. The markup half is pinned in the component suite; this is
    // the half jsdom cannot see.
    // Through the same helper as the rest, not a match on the first block
    // for the selector: a later `@media (max-width: 40rem)` dropping the
    // rule is exactly the change this is watching for, and on exactly the
    // width the overflow fix is about.
    const blocks = blocksFor(".exact-tabs");
    stands(blocks, /display:\s*flex\s*;/, /display\s*:/);
    stands(blocks, /border-bottom:/, /border-bottom(-color|-width|-style)?\s*:|border\s*:/);
  });

  it("keeps the tab strip from widening the page it is on", () => {
    // `overflow-x: auto` only zeroes a scroll container's automatic minimum
    // size when it is a FLEX item; this strip is a block child of the list,
    // so without containment its min-content — the sum of the tabs — travels
    // up to the host's flex column and stretches it past the window. Measured
    // at 390px: a 569px document, with the right edge of every card off it.
    const blocks = blocksFor(".exact-tabs");
    expect(blocks.length).toBeGreaterThan(0);
    // Declared SOMEWHERE, and not taken back after that — rather than
    // declared in the first block for the class. Adding an earlier, harmless
    // rule (`scrollbar-width: thin`, say) is not a regression, and a test
    // that goes red for it teaches the next person to distrust it.
    stands(blocks, /contain:\s*inline-size\s*;/, /contain\s*:/);
    stands(blocks, /overflow-x:\s*auto\s*;/, /overflow(-x)?\s*:/);
    // A block-level flex row is what makes the rule span the line and the
    // contents scroll; either one changed and the containment is moot.
    stands(blocks, /display:\s*flex\s*;/, /display\s*:/);
    // A strip that scrolls is a strip that can hand the gesture on: swiped
    // past its end on a touchpad, scroll chaining walks out to the host's
    // page and moves it sideways under the reader's finger.
    stands(blocks, /overscroll-behavior-x:\s*contain\s*;/, /overscroll-behavior(-x)?\s*:/);
    // The strip corrects its own scroll position, and a host's
    // `* { scroll-behavior: smooth }` would make that an animation whose
    // intermediate events read as the reader scrolling — after which the
    // strip stops correcting itself at all.
    stands(blocks, /scroll-behavior:\s*auto\s*;/, /scroll-behavior\s*:/);
  });

  it("never contains the list itself", () => {
    // Inline-size containment on `.exact-list` — the element a host mounts —
    // computes its width as if it had no contents, collapsing the whole list
    // to its padding in any host that sizes the remote by content. The
    // sibling assertion above forbids `container-type` there for the same
    // reason; `contain` reaches it by a different word.
    for (const [selector, body] of rulesIn(css)) {
      const last = selector.split(",").map((one) => one.trim().split(/[\s>+~]+/).at(-1) ?? "");
      // `.exact-root` as well: containment there collapses everything below
      // it, the list included, by exactly the same route.
      //
      // The compound on its own, not merely containing the class:
      // `.exact-root.exact-tooltip-layer` is a body-level layer and
      // `.exact-root.exact-detail` is another page — containing either is
      // nobody's bug, and a red test about collapsing the mounted list
      // would be a lie about both.
      if (!last.some((one) => /^\.exact-(list|root)$/.test(one))) continue;
      expect([selector, body]).not.toMatchObject([
        expect.anything(),
        expect.stringMatching(/(?:container-type|contain)\s*:/),
      ]);
    }
  });

  it("sizes the sort segments from their labels, not into equal thirds", () => {
    // Measured in a browser, because jsdom lays nothing out: the group is
    // content-sized at 519px, and `flex: 1 1 0` split that equally — 173px
    // each against a longest label of 190 — so the longest always starved
    // and its button overhung the slot (#554). From content the same 519px
    // goes 190/188/140 and every label is whole, in the wide row and the
    // narrow one alike.
    //
    // `1 1 auto` exactly: `1 1 0` is the bug, and a bare `flex: 1` means
    // `1 1 0%`, which is the same thing spelled shorter.
    //
    // The longhands are in the `undone` pattern because they say the same
    // thing one word at a time: `flex-basis: 0` alone restores the bug
    // exactly, and a test watching only the shorthand passes over it.
    // `width` is in `undone` because with `flex-basis: auto` the base size
    // comes from it: `width: 0` on the slot IS `flex: 1 1 0`, and measured,
    // it is worse than the bug — all three labels clipped, not two.
    stands(
      blocksFor(...SEGMENT),
      /flex:\s*1 1 auto\s*;/,
      /flex(-grow|-shrink|-basis)?\s*:|width\s*:/,
    );
  });

  it("lets a labelled segment's button shrink, and leaves the icon ones alone", () => {
    // The other half of the fix, and the half that carries the narrow row.
    // The slot is `inline-flex` (`.exact-action-tip`), so the button is a
    // flex item and its automatic minimum size is its min-content: without
    // this it cannot shrink into a slot narrower than its label, and it
    // hangs out of the group, which clips it. Measured at a 278px column:
    // 12.9px of the third label with three orders, 27.2px of the fourth
    // with a host-supplied one, and 0 with this.
    //
    // Scoped to the labelled group, because the view-mode segments hold a
    // bare 20px icon with nothing to ellipsise and the padding would squeeze
    // the icon instead — 15.5px at a 90px column, 0 at 50px.
    const buttons = blocksFor(".exact-seg__btn");
    stands(buttons, /min-width:\s*0\s*;/, /min-width\s*:/);
    const scoped = buttons.filter(([, body]) => /min-width:\s*0/.test(body));
    expect(scoped).toHaveLength(1);
    expect(css).toMatch(/\.exact-seg--grow\s+\.exact-seg__btn\s*\{[^}]*min-width:\s*0/);
  });

  it("lets the row's actions wrap rather than overflow", () => {
    // Three buttons that cannot wrap are 364px of row in a 278px column —
    // and what actually removes the overflow is that wrapping drops their
    // min-content contribution from that sum to the widest single button.
    const blocks = blocksFor(".exact-list__triggers");
    expect(blocks.length).toBeGreaterThan(0);
    // `wrap;` exactly: `wrap-reverse` also matches a loose test, and it
    // paints the lines bottom-up, so the visual order stops matching the tab
    // order (WCAG 2.4.3, the rule the controls row is arranged around).
    stands(blocks, /flex-wrap:\s*wrap\s*;/, /flex-wrap\s*:/);
    stands(blocks, /display:\s*flex\s*;/, /display\s*:/);
    // Wrapped, the short second line has to stay under the first: ragged
    // left, the actions read as a new column rather than a continuation.
    stands(blocks, /justify-content:\s*flex-end\s*;/, /justify-content\s*:/);
  });

  /** Every `@media`/`@container` block in the sheet, as `[prelude, body]`.
   *  Brace-matched rather than regex-cut: a lazy `[^@]*?` runs straight
   *  through the end of one block into the next and reports rules that are
   *  not in it. */
  const atRules = () => {
    const found: [string, string][] = [];
    const start = /@(?:media|container)[^{]*\{/g;
    let match: RegExpExecArray | null;
    while ((match = start.exec(css))) {
      let depth = 1;
      let i = start.lastIndex;
      while (i < css.length && depth > 0) {
        if (css[i] === "{") depth++;
        else if (css[i] === "}") depth--;
        i++;
      }
      found.push([match[0], css.slice(start.lastIndex, i - 1)]);
      start.lastIndex = i;
    }
    return found;
  };

  it("asks the container, not the window, how much room there is", () => {
    // A 1280px window gives this remote a ~900px column in ht-phr and the
    // full width in CB. A viewport query would put three orders on one line
    // in a column too narrow to hold them, and the control would hang out of
    // the host's column.
    // Width queries only: `@media (forced-colors: active)` also styles these
    // classes and is right to ask the window, since the mode is the user's,
    // not the column's. Asking after a width is what has to go through the
    // container.
    const sized = atRules().filter(
      ([prelude, body]) =>
        /\.exact-(seg|list__sort)/.test(body) && /\bm(in|ax)-width\b|\bwidth\s*[<>:]/.test(prelude),
    );
    expect(sized.length).toBeGreaterThan(0);
    for (const [prelude] of sized) expect(prelude).toMatch(/^@container exact-controls /);
  });
});
