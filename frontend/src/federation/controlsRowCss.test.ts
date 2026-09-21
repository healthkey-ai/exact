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
