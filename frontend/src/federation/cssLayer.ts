/* Put everything this remote injects into the host page inside one cascade
 * layer.
 *
 * `injectStyles` appends our compiled stylesheet to the *host's* `<head>`, and
 * our `@layer utilities` block merged into the host's layer of the same name —
 * later in source order, so an equal-specificity utility of ours won. A bare
 * `.hidden` from this bundle beat HealthTree ONE's `hidden lg:flex` header:
 * the logo, chat, bell and Donate button disappeared on every screen this
 * remote mounted on, and the mobile hamburger appeared in their place.
 *
 * Hosts declare the order before anything else:
 *
 *     @layer properties, theme, base, components, mf-remote, utilities;
 *
 * so `mf-remote` sits above the host's preflight — our own screens still style
 * themselves — and below the host's utilities, which the host's chrome is
 * built from. Our internal layer order survives as sub-layers of it, and rules
 * we ship unlayered stay the strongest thing we have.
 *
 * This is not free, and the trade is deliberate. Per the cascade-layers spec
 * an *unlayered* normal declaration beats every layered one regardless of
 * specificity, so on a host that declares no layer order — or that ships an
 * unlayered preflight, as Tailwind v3 does — rules of ours that used to win on
 * source order now lose to it. We accept that: losing a button background is
 * recoverable, erasing the host's header is not, and every remote owes the
 * host the same contract. Hosts that want the old behaviour declare the layer
 * order above, which puts `mf-remote` where we can style ourselves again.
 *
 * `@property`, `@keyframes` and `@font-face` are hoisted back out. Layers do
 * not change how any of them register, and `@property` inside a layer is not
 * honoured in every browser. `@charset` is hoisted with them for tidiness; it
 * is inert inside a `<style>` element either way.
 *
 * Known limit: hoisting inspects only the top level, so a `@keyframes` or
 * `@property` nested inside `@media`/`@supports` stays in the layer. Nothing
 * in `exact.css` does that today; recursing would mean splitting conditional
 * group rules, which is a bigger change than the case warrants.
 */

const HOISTED = /^@(?:property|(?:-webkit-|-moz-|-o-)?keyframes|font-face|charset)\b/i;

/* `@import` cannot be layered by wrapping. Inside a `@layer` block it is
 * invalid and the parser drops it silently, and hoisting it out would put a
 * whole external sheet *above* the layer we just moved ourselves into —
 * exactly the failure this module exists to prevent. Neither is a safe silent
 * default, so a sheet carrying one is passed through untouched and noisily. */
const UNLAYERABLE = /^@import\b/i;

function bail(reason: string): void {
  console.warn(
    `[exact-remote] injected CSS left unlayered (${reason}); ` +
      "the host's cascade layers will not contain this sheet.",
  );
}

export function layerRemoteCss(css: string, layer = "mf-remote"): string {
  const rules = topLevelRules(css);
  // Parsing failed, so we cannot tell rules apart. Emitting a wrapper around
  // a sheet we misread is worse than emitting the sheet: fall back to the
  // pre-layer behaviour rather than corrupting the cascade.
  if (rules === null) return css;

  const hoisted: string[] = [];
  const layered: string[] = [];

  for (const rule of rules) {
    const start = rule.replace(/^(?:\s|\/\*[\s\S]*?\*\/)+/, "");
    if (UNLAYERABLE.test(start)) {
      bail("it contains an @import");
      return css;
    }
    (HOISTED.test(start) ? hoisted : layered).push(rule);
  }

  const body = layered.join("").trim();
  return [hoisted.join("").trim(), body && `@layer ${layer}{${body}}`]
    .filter(Boolean)
    .join("\n");
}

/** Split a stylesheet into its top-level rules: balanced `{}`, or a `;` at
 *  depth zero. Comments and quoted strings are skipped rather than scanned —
 *  an apostrophe in a comment ("the host's own preflight") would otherwise
 *  open a string that swallows the rest of the file.
 *
 *  Returns `null` when the sheet does not parse as balanced, rather than
 *  guessing. The split must also be lossless: the pieces are contiguous
 *  slices, so `rules.join("") === css` is a cheap end-to-end check that no
 *  future edit to this function can quietly drop or duplicate a byte. */
function topLevelRules(css: string): string[] | null {
  const rules: string[] = [];
  let depth = 0;
  let start = 0;
  let quote: string | null = null;

  for (let i = 0; i < css.length; i++) {
    const c = css[i];

    // A backslash escapes the next character in a selector as much as in a
    // string: `.data-\[selected\=\'true\'\]` is a real utility class in this
    // bundle, and reading its `\'` as an opening quote swallowed everything
    // after it (the @property rules stopped looking top-level).
    if (c === "\\") {
      i++;
      continue;
    }
    if (quote) {
      if (c === quote) quote = null;
      continue;
    }
    if (c === "/" && css[i + 1] === "*") {
      const end = css.indexOf("*/", i + 2);
      // A comment that never closes swallows whatever we append after it: the
      // layer's own closing `}` would land inside it, leaving the `@layer`
      // block unterminated and the browser dropping rules that the sheet as
      // written still applies.
      if (end === -1) {
        bail("it has an unterminated comment");
        return null;
      }
      i = end + 1;
      continue;
    }
    if (c === '"' || c === "'") {
      quote = c;
    } else if (c === "{") {
      depth++;
    } else if (c === "}") {
      // A close brace at depth zero means we lost track somewhere. The
      // previous version clamped to zero and carried on, which pushed every
      // remaining rule *outside* the layer — silently, and at higher priority
      // than everything inside it.
      if (depth === 0) {
        bail("it has an unbalanced `}`");
        return null;
      }
      if (--depth === 0) {
        rules.push(css.slice(start, i + 1));
        start = i + 1;
      }
    } else if (c === ";" && depth === 0) {
      rules.push(css.slice(start, i + 1));
      start = i + 1;
    }
  }

  if (start < css.length) rules.push(css.slice(start));

  if (depth !== 0 || quote !== null) {
    bail(depth !== 0 ? "it has an unclosed `{`" : "it has an unterminated string");
    return null;
  }
  if (rules.join("") !== css) {
    bail("the rule split was not lossless");
    return null;
  }
  return rules;
}
