// Reading a stylesheet the way the cascade reads it.
//
// Layout that depends on width cannot be tested by rendering — jsdom lays
// nothing out, so a container query that matches nothing looks exactly like
// one that matches — which leaves reading the sheet as text. Doing that
// honestly means modelling two things the eye skips: what is a declaration
// (not one inside a comment or a string) and which declaration WINS.
//
// Pulled out of `controlsRowCss.test.ts` so the model itself can be tested
// against sheets written to break it, rather than only against the one sheet
// it happens to read.

/** Comments out, because a declaration inside one is not a declaration:
 *  commenting either of the rules below out left every assertion green.
 *  Scanned rather than replaced, because `content: "/*"` is a string, not
 *  the start of a comment — a regex takes it for one and swallows the sheet
 *  to the next `*` + `/`, hiding every override in between. */
export const uncommented = (src: string) => {
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

/** Every declaration block in a sheet, as `[selector, body]` — inside
 *  at-rules too, since a `@media` override is still an override. An at-rule
 *  prelude never pairs with a `}` of its own, so it never reads as one. */
export const rulesIn = (css: string) => [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => [m[1].trim(), m[2]] as const);

/** Split a selector list on the commas that SEPARATE branches — not the ones
 *  inside `:is(...)`, `:not(...)` or an attribute value. Split naively,
 *  `:is(#nope, .exact-root) .x` tears into `:is(#nope` and `.exact-root) .x`,
 *  and only the second fragment is weighed: the id vanishes, the rule reads
 *  as a tie, and a selector that really does win from above is dismissed. */
export const branches = (selector: string) => {
  const out: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let current = "";
  for (const c of selector) {
    if (quote) {
      current += c;
      if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'") quote = c;
    else if (c === "(" || c === "[") depth += 1;
    else if (c === ")" || c === "]") depth -= 1;
    else if (c === "," && depth === 0) {
      out.push(current.trim());
      current = "";
      continue;
    }
    current += c;
  }
  out.push(current.trim());
  return out.filter((one) => one !== "");
};

/** How hard ONE selector pushes, as a single number. Ids, then classes and
 *  pseudo-classes and attributes, then elements — the cascade's own order,
 *  flattened by a base wide enough that no realistic selector carries.
 *
 *  One branch, never a list: in a list each branch has its own specificity
 *  and the one that decides is the branch that matched.
 *
 *  The functional pseudo-classes weigh as their ARGUMENT, not as themselves:
 *  `:not()`, `:is()` and `:has()` take their most specific argument and add
 *  nothing of their own, and `:where()` adds nothing at all. Counting the
 *  wrapper as a class AND its argument inflated every one of them — this
 *  sheet's own `.exact-seg__item:not(:last-child)` read as three classes
 *  where the cascade sees two, which is exactly how a real equal-specificity
 *  override gets dismissed as lighter and a regression slips past. */
const counted = (one: string) => {
  // `:before` and its three siblings are pseudo-ELEMENTS in their legacy
  // one-colon spelling, and weigh as elements however they are written. The
  // `(?<!:)` keeps that from also eating the tail of `::before`; measured,
  // nothing depends on it today, because the stray colon a greedy match
  // would leave behind matches neither pattern below.
  const legacy = /(?<!:):(before|after|first-line|first-letter)\b/g;
  const bare = one.replace(legacy, " ");
  const attributes = bare.match(/\[[^\]]*\]/g) ?? [];
  // Attribute VALUES are text, not selector: `[href="#top"]` holds no id and
  // `[title="x y"]` holds no element, but a regex reading the raw string
  // finds both and weighs the selector above what the cascade gives it —
  // the direction that dismisses a real override as lighter.
  const outside = bare.replace(/\[[^\]]*\]/g, " ");
  const ids = (outside.match(/#[\w-]+/g) ?? []).length;
  const classes = (outside.match(/\.[\w-]+|(?<!:):(?!:)[\w-]+/g) ?? []).length + attributes.length;
  const elements =
    (outside.match(/(^|[\s>+~])[a-z][\w-]*|::[\w-]+/g) ?? []).length +
    (one.match(legacy) ?? []).length;
  return ids * 10000 + classes * 100 + elements;
};

/** `:not(x)` → `x`, `:where(x)` → nothing. A list inside one of them
 *  collapses to its heaviest branch, which is what "the most specific
 *  argument" means.
 *
 *  Scanned to the matching parenthesis rather than matched with `[^()]*`:
 *  `:not(:nth-child(2))` nests, and a regex that cannot see past the inner
 *  pair leaves the whole thing wrapped — counting `:not` as a class on top
 *  of its argument, which weighs the declaration heavier than the cascade
 *  does and dismisses a real override as lighter. */
const unwrapped = (one: string) => {
  const WRAP = /:(where|not|is|has|matches|any)\(/i;
  let out = one;
  // Bounded: each pass removes one pair of parentheses.
  for (let pass = 0; pass < 32; pass += 1) {
    const at = out.search(WRAP);
    if (at === -1) break;
    const name = WRAP.exec(out.slice(at))![1].toLowerCase();
    const open = out.indexOf("(", at);
    let depth = 0;
    let close = -1;
    for (let i = open; i < out.length; i += 1) {
      if (out[i] === "(") depth += 1;
      else if (out[i] === ")") {
        depth -= 1;
        if (depth === 0) {
          close = i;
          break;
        }
      }
    }
    if (close === -1) break;
    const inner = out.slice(open + 1, close);
    const keep =
      name === "where"
        ? ""
        : branches(inner).reduce(
            (best, branch) => (counted(unwrapped(branch)) > counted(unwrapped(best)) ? branch : best),
            "",
          );
    out = out.slice(0, at) + unwrapped(keep) + out.slice(close + 1);
  }
  return out;
};

export const weigh = (one: string) => counted(unwrapped(one));

/** Every block that styles THE element carrying a class, in source order,
 *  as `[selector, body, weight]`.
 *
 *  Matched on the class rather than one spelling of the selector: a later
 *  `.exact-root .exact-list .exact-tabs` outranks the original and would
 *  slip past a test looking for the string it was written with. Only the
 *  last compound counts — `.exact-tabs button` styles the buttons.
 *
 *  The weight is of the MATCHING branches only, and the heaviest of them,
 *  because any of them can be the one that matches this element. Taken
 *  across the whole list instead, `.exact-seg__item, #unrelated` would
 *  read as id-weight here, and a rule that really does override its
 *  `flex` would be dismissed as lighter — the test passing over the
 *  regression it exists to catch. */
export const blocksIn = (
  css: string,
  ...names: readonly string[]
): readonly (readonly [string, string, number])[] => {
  // Several, because an element carries several classes and a rule reaching
  // it by ANY of them styles it: `.exact-seg__item` is also
  // `.exact-action-tip`, and a rule hung on the second is invisible to a
  // search for the first while overriding it just the same.
  const tokens = Object.fromEntries(
    names.map((n) => [n, new RegExp(`\\${n}(?![\\w-])`)]),
  );
  // The last compound of a branch, kept WHOLE.
  //
  // Two ways to get this wrong, and they fail in opposite directions.
  // Splitting on every combinator treats the space in
  // `:not(:nth-child(1), :nth-child(2))` as one, so the compound comes out
  // as a fragment and the rule is never seen. Stripping the parentheses
  // instead loses `:is(#unused, .exact-seg__item)`, where the class the
  // rule reaches the element BY is inside the argument — and that one
  // carries id weight, so the test stays green while the fix is overridden.
  //
  // So: split only on combinators at depth zero, and keep the arguments.
  const lastCompound = (one: string) => {
    let depth = 0;
    let quote: string | null = null;
    let start = 0;
    for (let i = 0; i < one.length; i += 1) {
      const c = one[i];
      if (quote) {
        if (c === quote) quote = null;
      } else if (c === '"' || c === "'") quote = c;
      else if (c === "(" || c === "[") depth += 1;
      else if (c === ")" || c === "]") depth -= 1;
      else if (depth === 0 && /[\s>+~]/.test(c)) start = i + 1;
    }
    const compound = one.slice(start);
    // `:not(x)` and `:has(x)` say what the element is NOT, or what is under
    // it — the subject is not `x`, so a class named there does not make the
    // rule one that styles it.
    let out = compound;
    for (let pass = 0; pass < 16; pass += 1) {
      const next = out.replace(/:(?:not|has)\([^()]*\)/g, "");
      if (next === out) break;
      out = next;
    }
    return out;
  };

  const hits = (selector: string) =>
    branches(selector).filter((one) => names.some((n) => tokens[n].test(lastCompound(one))));
  return rulesIn(css)
    .map(([selector, body]) => [selector, body, hits(selector)] as const)
    .filter(([, , matching]) => matching.length > 0)
    .map(
      ([selector, body, matching]) =>
        [selector, body, Math.max(...matching.map(weigh))] as const,
    );
};

/** Whether the declaration this regex finds carries `!important`.
 *
 *  Read from the match to the end of ITS declaration, not the whole block: a
 *  `!important` two declarations later says nothing about this one. */
export const importantAt = (body: string, which: RegExp) => {
  // EVERY declaration the pattern finds, not the first: a block can say the
  // same property twice — `flex: 0 1 auto; flex: 1 1 0 !important` — and it
  // is the second one that takes the cascade. Reading only the first reports
  // "not important" for a block that shouts.
  const all = new RegExp(which.source, which.flags.includes("g") ? which.flags : `${which.flags}g`);
  for (let found = all.exec(body); found; found = all.exec(body)) {
    const rest = body.slice(found.index);
    const end = rest.indexOf(";");
    if (/!\s*important/i.test(end === -1 ? rest : rest.slice(0, end + 1))) return true;
    if (all.lastIndex <= found.index) all.lastIndex = found.index + 1;
  }
  return false;
};
