// Ported from CancerBot's canonical shared/safeUrl.ts (#5045).
// Comment sync: CancerBot commit 85feb7fcb9b5ddaea2813be43e79f0d5ed4ed60e,
// cancerbot-org/cancerbot#5436 / #5439, following the React 19 migration #5289.
//
// The body beginning "A URL that arrives from the API" stays byte-identical to
// the corresponding section of that source. Only comments changed in this sync;
// safeHref/safeRedirect, their allow-list and two-base resolution are unchanged.
//
// `safeRedirect` is unused in EXACT today. It is retained for source parity,
// rather than maintaining a different variant of the rule in this repository.
//
// EXACT's use is #406: TrialDetailPage's NctLink gates the anchor on safeHref's
// result before rendering the corpus-supplied data.link.
//
// The React-version explanation below applies to both repositories: React 19
// blocks javascript: hrefs, but that does not replace the product URL policy.
//
// Repository-specific notes in the copied body describe CancerBot. In particular,
// the DRF URLField rationale for ftp/ftps refers to CB's StudyInfo fields, not
// EXACT's Trial.link: ours is models.TextField(default='') with no URL validator.
// Keep the shared allow-list here; narrowing it is a separate policy change,
// not part of synchronising React-version comments. The auth-redirect examples
// in safeRedirect's documentation are also CancerBot-specific.

// A URL that arrives from the API is not automatically safe to put in an `href`.
// React 18 only warned about `javascript:` URLs; React 19 blocks them. That is
// not a replacement for this guard: React still allows `data:` and does not
// enforce our scheme or same-site-relative-link policy. Trial links are written
// upstream, so validate them before rendering (#4955, same threat model as the
// tooltip XSS in #4932/#4934).

// Schemes a link in this product may use. `ftp`/`ftps` are here because the API
// stores these fields as a DRF `URLField`, whose validator accepts them — leaving
// them out would silently turn valid stored links into inert text. None of the
// six — http, https, ftp, ftps, mailto, tel — can execute script. (This said
// "five" from #4955 until #5045; the set has always had six members, and a
// count nobody can check against the line below is worse than no count.)
// `data:` and `blob:` are absent deliberately, and `javascript:`/`vbscript:`
// are the attack.
const ALLOWED_SCHEMES = new Set(['http:', 'https:', 'ftp:', 'ftps:', 'mailto:', 'tel:']);

// Does the value carry its own scheme? Anything that does not has to prove it is
// the relative link it looks like -- see the two bases below.
const HAS_SCHEME = /^[a-z][a-z0-9+.\-]*:/i;

// Two bases, not one, and never returned or rendered.
//
// The question a scheme-less value has to answer is "are you a path on this
// site, or do you name your own authority?" -- and the only thing that can
// answer it reliably is the parser, because the parser is what the browser will
// use. A single base plus `parsed.origin !== BASE` looks like it answers it but
// does not: the input can simply spell the base's host. A literal `/^[/\\]{2}/`
// on the raw string does not either -- the WHATWG parser strips ASCII tab, LF
// and CR from *anywhere* in the input and leading C0 controls, none of which
// `String.trim()` removes, so `/\t/evil.com` is authority-relative to the parser
// and invisible to the regex. Both of those were shipped and both were wrong.
//
// Resolving against two different hosts settles it with no string inspection at
// all: a genuine relative path takes its host from whichever base it was given,
// so the two resolutions differ. A value naming its own authority resolves to
// that same host both times, whatever the authority is -- including either
// base's own host, which is what makes this unspoofable rather than merely
// harder to spoof.
const RESOLUTION_BASE_A = 'https://a.invalid';
const RESOLUTION_BASE_B = 'https://b.invalid';

/**
 * The URL if it is safe to use as an `href`, otherwise `undefined`.
 *
 * `undefined` rather than `'#'` or `''`: an anchor with no `href` is not a link
 * at all. Call sites must gate the whole anchor on the *result*, not on the raw
 * value — rendering `<a>` without an `href` still paints link styling, giving a
 * control that looks clickable and silently does nothing.
 */
export function safeHref(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;

  const trimmed = value.trim();
  if (!trimmed) return undefined;

  let parsed: URL;
  try {
    parsed = new URL(trimmed, RESOLUTION_BASE_A);
  } catch {
    return undefined;
  }

  // Compared against the *parsed* protocol, so the leading-whitespace,
  // embedded-newline and mixed-case tricks (`java\nscript:`, `JaVaScRiPt:`) are
  // normalised away before the check rather than after it.
  if (!ALLOWED_SCHEMES.has(parsed.protocol)) return undefined;

  if (!HAS_SCHEME.test(trimmed)) {
    // A value with no scheme is supposed to be a path on this site. See the note
    // on the two bases above for why this is the whole check and not one half of
    // a pair -- a second, literal check would duplicate part of this one and
    // silently take over its test coverage, which is what happened last time.
    let a: URL;
    let b: URL;
    try {
      a = new URL(trimmed, RESOLUTION_BASE_A);
      b = new URL(trimmed, RESOLUTION_BASE_B);
    } catch {
      return undefined;
    }
    if (a.host === b.host) return undefined;
  }

  return trimmed;
}

/**
 * The URL if it is safe to *navigate to*, otherwise `undefined`.
 *
 * Stricter than `safeHref`, because the sinks differ: an `href` the user has to
 * click can be a relative path and can reasonably be `mailto:`, while
 * `window.location.href = x` happens without a click and only ever means "leave
 * for another site over HTTPS" here (the Google and Epic authorize endpoints).
 *
 * Scope, stated because the first version of this change overclaimed: this is
 * not an origin allowlist. Both values come from our own auth endpoints rather
 * than from a query parameter, and the Epic authorize host is per-organisation,
 * so it cannot be enumerated in the client. What this rules out is the class the
 * ticket is about — a non-HTTPS scheme, `javascript:` above all, reaching a
 * navigation — plus any relative or protocol-relative spelling, which a genuine
 * authorize URL never has.
 */
export function safeRedirect(value: unknown): string | undefined {
  const href = safeHref(value);
  if (href === undefined) return undefined;
  // `^https://`, not `startsWith('https:')`. A bare `https:evil.com` has the
  // right scheme but no authority of its own, so the browser resolves it against
  // the current page -- meaning what it points at depends on where it is
  // evaluated. A real authorize URL is absolute; require that literally rather
  // than describe it in a docstring.
  return /^https:\/\//i.test(href) ? href : undefined;
}
