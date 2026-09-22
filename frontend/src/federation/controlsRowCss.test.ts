/** The controls row's CSS, read as text.
 *
 *  Layout that depends on width cannot be tested by rendering: jsdom lays
 *  nothing out, so a container query that matches nothing looks exactly like
 *  one that matches. These two assertions are about the sheet itself, and
 *  they cover the two ways this row goes quietly wrong.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

describe("the controls row", () => {
  const raw = readFileSync(new URL("./exact.css", import.meta.url), "utf8");

  /** Comments out, because a declaration inside one is not a declaration:
   *  commenting either of the rules below out left every assertion green.
   *  Scanned rather than replaced, because `content: "/*"` is a string, not
   *  the start of a comment — a regex takes it for one and swallows the sheet
   *  to the next `*` + `/`, hiding every override in between. */
  const uncommented = (src: string) => {
    let out = "";
    let i = 0;
    while (i < src.length) {
      const c = src[i];
      if (c === '"' || c === "'") {
        let j = i + 1;
        while (j < src.length && src[j] !== c) j += src[j] === "\\" ? 2 : 1;
        out += src.slice(i, j + 1);
        i = j + 1;
      } else if (/^url\(/i.test(src.slice(i, i + 4))) {
        // Nor inside `url()`, where `/*` is part of a path: `url(/img/a/*.svg)`
        // would otherwise swallow the sheet to the next `*` + `/`, hiding
        // every rule in between — the same failure as above, one token later.
        const end = src.indexOf(")", i);
        const stop = end === -1 ? src.length : end + 1;
        out += src.slice(i, stop);
        i = stop;
      } else if (c === "/" && src[i + 1] === "*") {
        const end = src.indexOf("*/", i + 2);
        i = end === -1 ? src.length : end + 2;
      } else {
        out += c;
        i += 1;
      }
    }
    return out;
  };
  const css = uncommented(raw);

  /** Every declaration block in the sheet, as `[selector, body]` — inside
   *  at-rules too, since a `@media` override is still an override. An at-rule
   *  prelude never pairs with a `}` of its own, so it never reads as one. */
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(
    (m) => [m[1].trim(), m[2]] as const,
  );

  /** Every block that styles THE element carrying a class, in source order.
   *  Matched on the class rather than one spelling of the selector: a later
   *  `.exact-root .exact-list .exact-tabs` outranks the original and would
   *  slip past a test looking for the string it was written with. Only the
   *  last compound counts — `.exact-tabs button` styles the buttons. */
  const blocksFor = (className: string) => {
    const token = new RegExp(`\\${className}(?![\\w-])`);
    return rules
      .filter(([selector]) =>
        selector
          .split(",")
          .some((one) => token.test(one.trim().split(/[\s>+~]+/).at(-1) ?? "")),
      )
      .map(([, body]) => body);
  };

  /** A declaration that is made, and not taken back by any later block for
   *  the same element. */
  const stands = (blocks: string[], declared: RegExp, undone: RegExp) => {
    const at = blocks.findIndex((block) => declared.test(block));
    expect(at, `nothing declares ${declared}`).toBeGreaterThanOrEqual(0);
    for (const block of blocks.slice(at + 1)) expect(block).not.toMatch(undone);
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
    for (const [selector, body] of rules) {
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
