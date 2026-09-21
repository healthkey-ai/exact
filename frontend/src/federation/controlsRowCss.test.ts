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
  const css = readFileSync(new URL("./exact.css", import.meta.url), "utf8");

  it("declares its query container on the element that carries both classes", () => {
    // The list root is `<div class="exact-root exact-list">`, so the
    // descendant form every other rule in the sheet uses — `.exact-root
    // .exact-list` — matches nothing here. Written that way the container
    // never exists, and each `@container` rule below silently never applies:
    // nothing throws, nothing logs, the row is simply always the narrow one.
    expect(css).toMatch(/\.exact-root\.exact-list\s*\{[^}]*container-type:\s*inline-size/);
    expect(css).not.toMatch(/\.exact-root \.exact-list\s*\{[^}]*container-type/);
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
    const sized = atRules().filter(([, body]) => /\.exact-(seg|list__sort)/.test(body));
    expect(sized.length).toBeGreaterThan(0);
    for (const [prelude] of sized) expect(prelude).toMatch(/^@container exact-list /);
  });
});
