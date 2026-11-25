// Who a credential names, in a form two moments apart can be compared.
//
// EXACT attributes a write to whoever the bearer token names, so a queued
// payload belongs to the reader who produced it and must not be sent under
// somebody else's credential. The write queues capture this at enqueue and
// compare it at send; see `patientWriter.ts` and exact#583 for the four
// measured shapes a React key cannot catch.
//
// A TOKEN THAT CHANGED IS NOT AN IDENTITY THAT CHANGED. Firebase hands out a
// fresh JWT roughly hourly for the same person, so comparing the credential
// as a string turns a routine refresh into a dropped edit — the reader saves
// a haemoglobin, their token rotates during the debounce, and the row says it
// could not be written. What has to match is the person inside it.

/** The identity a credential names, or `undefined` when it names nothing.
 *
 *  Three answers, and the third is the interesting one:
 *
 *    * `undefined` — no credential. NOT "a different identity": see
 *      `sameIdentity`.
 *    * `sub:<iss>|<sub>` — a JWT we could read. The issuer travels with the
 *      subject because two providers can both call somebody `1`.
 *    * `raw:<token>` — anything else: an opaque token, a JWT with no `sub`,
 *      a payload that is not JSON. Comparison falls back to the whole string,
 *      so a rotation of an opaque credential reads as a change and the write
 *      is dropped. That is the conservative direction on purpose — a dropped
 *      edit is reported to the reader, a misattributed one is not, and an
 *      opaque credential gives nothing else to go on.
 */
export function fingerprintOf(token: string | undefined | null): string | undefined {
  if (typeof token !== "string") return undefined;
  const trimmed = token.trim();
  if (trimmed === "") return undefined;
  const claims = claimsOf(trimmed);
  if (claims === undefined) return `raw:${trimmed}`;
  const sub = typeof claims.sub === "string" ? claims.sub : undefined;
  const iss = typeof claims.iss === "string" ? claims.iss : undefined;
  // A JWT without a subject names nobody we can compare, so it is treated as
  // opaque rather than as "no identity" — which would disable the guard.
  if (sub === undefined) return `raw:${trimmed}`;
  return `sub:${iss ?? "-"}|${sub}`;
}

/** Asked for a credential, and there wasn't one.
 *
 *  NOT the same as `undefined`, and the difference is the whole of the hole
 *  this closes. `undefined` means "nothing known" — nobody has looked yet,
 *  or this host has no credentials at all — and `sameIdentity` waves it
 *  through so a no-auth host keeps working. A reader that HAS a credential
 *  and is between two of them produces the same empty answer from
 *  `getToken`, and that interval is exactly a sign-out followed by a
 *  sign-in: the middle of the account switch this whole mechanism exists to
 *  catch. Recorded as `undefined`, the guard switched itself off precisely
 *  there, and a write enqueued in that window went out under the next
 *  account. Measured through the real bridge.
 *
 *  So a caller that ASKED records this instead. It compares unequal to any
 *  real identity, and equal to itself — so a host that never has a
 *  credential sees it on every read and is unaffected, which is the carve-out
 *  `sameIdentity` documents. */
export const NO_CREDENTIAL = "#no-credential";

/** Asked, and the asking itself failed.
 *
 *  THE THIRD STATE, and it is here because leaving it out cost four review
 *  rounds. "Who is this request for" has three answers, not two:
 *
 *    a fingerprint      — we know.
 *    `NO_CREDENTIAL`    — we asked; this deployment has nobody signed in.
 *    `COULD_NOT_TELL`   — we asked and the ask threw. A token refresh that
 *                         failed, a network blip, a sign-out in flight.
 *
 *  The first two are knowledge. The third is not, and representing it as
 *  `undefined` merged it with "nothing to guard" — which `sameIdentity`
 *  matches against everybody, on purpose, so that hosts with no accounts
 *  keep working. Measured: one rejecting `getToken` at mount stamped the
 *  cache unknown, the stamp then matched every later reader for the life
 *  of the transport, and the first reader's saved filters were written
 *  wholesale into the second's row.
 *
 *  A string, and one nothing else can equal. `fingerprintOf` only ever
 *  produces `sub:…` or `raw:…`, but the readers themselves are
 *  host-supplied `() => string | undefined`, so the bare word "unknown"
 *  was a value a host could legitimately return and be mistaken for this.
 *  The `#` prefix costs nothing and removes the question. So it compares
 *  unequal to any real reader
 *  and equal to itself, which is what makes a cache stamped with it
 *  unusable by anybody — including, deliberately, by the reader it was
 *  actually read for. That costs one extra read and recovers by itself:
 *  the mismatch drops the cache, the next save re-reads, and that read's
 *  ask usually succeeds. */
export const COULD_NOT_TELL = "#could-not-tell";

/** Whether a payload queued under `queued` may be sent under `current`.
 *
 *  UNKNOWN IS NOT A MISMATCH. Either side being nullish answers yes, and
 *  that is a decision rather than an oversight: before anything has looked
 *  there is nothing to compare, and a guard that dropped writes on that
 *  would break every host at its first edit.
 *
 *  IT IS NOT THE SAME AS "NO CREDENTIAL". A caller that asked and got
 *  nothing records `NO_CREDENTIAL`, which compares unequal to a real
 *  identity — see the note there for the window that distinction closes.
 *  The no-auth host is still fine: it records `NO_CREDENTIAL` every time,
 *  and `NO_CREDENTIAL === NO_CREDENTIAL`.
 */
export function sameIdentity(
  queued: string | undefined | null,
  current: string | undefined | null,
): boolean {
  // `== null`, not `=== undefined`: a plain-JS host reader answering `null`
  // means the same thing and must not be read as a mismatch, which would
  // drop every one of its writes silently.
  if (queued == null || current == null) return true;
  return queued === current;
}

/** What a stranded write failed on.
 *
 *  Its own type because the callers tell it apart from a refusal: nothing
 *  was sent, the server never saw it, and a retry would be wrong — the
 *  credential that could have carried it is gone. `fields` is duck-typed
 *  the same way `patientWriter` reads a server error's, so a host that
 *  bundles this module twice still gets the list.
 */
export class IdentityChanged extends Error {
  readonly fields: string[];

  constructor(fields: string[] = []) {
    super(
      fields.length > 0
        ? `[exact] not written: the signed-in account changed while these were waiting — ${fields.join(", ")}`
        : "[exact] not written: the signed-in account changed while this was waiting",
    );
    this.name = "IdentityChanged";
    this.fields = fields;
  }
}

/** The claims of a JWT, or `undefined` if it is not one we can read.
 *
 *  Deliberately forgiving: this is a comparison aid, not a verifier. Nothing
 *  here checks a signature and nothing should — the server does that, and a
 *  client that refused an unverifiable token would be dropping the reader's
 *  edits over a decision it is not entitled to make.
 */
function claimsOf(token: string): Record<string, unknown> | undefined {
  const parts = token.split(".");
  if (parts.length !== 3) return undefined;
  const payload = decodeSegment(parts[1]);
  if (payload === undefined) return undefined;
  try {
    const parsed: unknown = JSON.parse(payload);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return undefined;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

/** One base64url segment as text, or `undefined`.
 *
 *  `atob` gives bytes-as-latin1, so a `sub` or an `iss` containing anything
 *  outside ASCII comes back mangled unless it is decoded as UTF-8. Mangled
 *  consistently would still compare equal — but two different names can
 *  mangle to the same string, and this is the value that decides whether a
 *  write reaches a stranger.
 */
function decodeSegment(segment: string): string | undefined {
  try {
    const base64 = segment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return undefined;
  }
}
