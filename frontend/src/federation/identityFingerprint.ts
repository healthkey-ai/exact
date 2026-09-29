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

/** Whether a payload queued under `queued` may be sent under `current`.
 *
 *  UNKNOWN IS NOT A MISMATCH. Either side being `undefined` answers yes, and
 *  that is a decision rather than an oversight: a host with no credential at
 *  all — the local no-auth stand, a deployment behind a gateway that injects
 *  the header — would otherwise have every edit dropped by a guard meant to
 *  protect it. This guard exists to stop a write reaching a KNOWN stranger,
 *  not to require a credential of hosts that never had one.
 */
export function sameIdentity(
  queued: string | undefined,
  current: string | undefined,
): boolean {
  if (queued === undefined || current === undefined) return true;
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
