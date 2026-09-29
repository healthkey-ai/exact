// What counts as the same person, and what counts as nobody.
//
// Two mistakes are possible here and they are not symmetric. Calling two
// identities the same sends one reader's edit into another's record, silently.
// Calling one identity two drops an edit, loudly — the row says it could not
// be written. So every case below says which way it is allowed to be wrong.
import { describe, expect, it } from "vitest";

import { IdentityChanged, fingerprintOf, sameIdentity } from "./identityFingerprint";

/** A JWT with the given claims. Unsigned — nothing here verifies one, and a
 *  test that signed them would be testing its own crypto. */
const jwt = (claims: Record<string, unknown>, signature = "sig") => {
  const b64 = (value: unknown) =>
    btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value))))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${b64({ alg: "RS256", typ: "JWT" })}.${b64(claims)}.${signature}`;
};

const FIREBASE = "https://securetoken.google.com/demo-healthkey";

describe("what a credential names", () => {
  it("reads the subject and the issuer out of a JWT", () => {
    expect(fingerprintOf(jwt({ iss: FIREBASE, sub: "9v2qPmXiGbVUCmEpwxCzn722Dgp7" }))).toBe(
      `sub:${FIREBASE}|9v2qPmXiGbVUCmEpwxCzn722Dgp7`,
    );
  });

  it("does not confuse two providers that number their users the same way", () => {
    // The reason the issuer is in there at all. `sub: "1"` is not exotic —
    // a self-hosted provider hands them out in order.
    const a = fingerprintOf(jwt({ iss: "https://a.example", sub: "1" }));
    const b = fingerprintOf(jwt({ iss: "https://b.example", sub: "1" }));
    expect(a).not.toBe(b);
  });

  it("says nothing about a credential that is not there", () => {
    for (const nothing of [undefined, null, "", "   ", 7 as never, {} as never]) {
      expect(fingerprintOf(nothing)).toBeUndefined();
    }
  });

  it("falls back to the whole string for a token it cannot read", () => {
    // An opaque session token — EXACT's own DRF `Token` is 40 hex characters
    // with no structure at all.
    const opaque = "dac289930e8724189cd5e2ea8f3a27a92375e259";
    expect(fingerprintOf(opaque)).toBe(`raw:${opaque}`);
  });

  it("treats a JWT with no subject as opaque, not as nobody", () => {
    // `undefined` would mean "no credential", which SWITCHES THE GUARD OFF
    // (see `sameIdentity`). A token naming nobody must not do that — it is a
    // credential, we simply cannot read who it is for, so it compares whole.
    const anonymous = jwt({ iss: FIREBASE });
    expect(fingerprintOf(anonymous)).toBe(`raw:${anonymous}`);
    expect(fingerprintOf(anonymous)).not.toBeUndefined();
  });

  it("treats a non-string subject as no subject", () => {
    // A provider sending `sub: 1` unquoted. Coercing it to "1" would make it
    // equal to a different provider's string "1"; refusing it falls back to
    // the whole token, which is safe in the direction that matters.
    const numeric = jwt({ iss: FIREBASE, sub: 1 });
    expect(fingerprintOf(numeric)).toBe(`raw:${numeric}`);
  });

  it("survives a subject that is not ASCII", () => {
    // `atob` alone gives latin1 bytes, so these mangle — and two different
    // names can mangle to the same string, which is the direction that
    // misattributes a write.
    const one = fingerprintOf(jwt({ iss: FIREBASE, sub: "Ольга" }));
    const two = fingerprintOf(jwt({ iss: FIREBASE, sub: "Олъга" }));
    expect(one).toBe(`sub:${FIREBASE}|Ольга`);
    expect(one).not.toBe(two);
  });

  it("does not read a token with the wrong number of parts", () => {
    for (const malformed of ["a.b", "a.b.c.d", "....", "."]) {
      expect(fingerprintOf(malformed)).toBe(`raw:${malformed}`);
    }
  });

  it("does not read a payload that is not a JSON object", () => {
    const b64 = (s: string) => btoa(s).replace(/=+$/, "");
    for (const payload of ['"a string"', "42", "[1,2]", "null", "{oops"]) {
      const token = `${b64("{}")}.${b64(payload)}.sig`;
      expect(fingerprintOf(token)).toBe(`raw:${token}`);
    }
  });

  it("ignores surrounding whitespace, which a header copy brings with it", () => {
    const token = jwt({ iss: FIREBASE, sub: "abc" });
    expect(fingerprintOf(`  ${token}\n`)).toBe(fingerprintOf(token));
  });
});

describe("whether a queued write may still be sent", () => {
  const one = fingerprintOf(jwt({ iss: FIREBASE, sub: "one" }));
  const two = fingerprintOf(jwt({ iss: FIREBASE, sub: "two" }));

  it("lets the same person through", () => {
    expect(sameIdentity(one, one)).toBe(true);
  });

  it("lets a ROTATED token through, which is the whole point", () => {
    // Firebase refreshes hourly. Compared as strings these are two
    // credentials; compared as identities they are one reader, mid-edit.
    const fresh = jwt({ iss: FIREBASE, sub: "one", exp: 2000, iat: 1000 });
    const staler = jwt({ iss: FIREBASE, sub: "one", exp: 9000, iat: 8000 });
    expect(fresh).not.toBe(staler);
    expect(sameIdentity(fingerprintOf(fresh), fingerprintOf(staler))).toBe(true);
  });

  it("stops a different person", () => {
    expect(sameIdentity(one, two)).toBe(false);
  });

  it("lets everything through when either side is unknown", () => {
    // The local no-auth stand, and any deployment behind a gateway that
    // injects the header. A guard that dropped these would break the hosts
    // it was built to protect, and it can protect nobody it cannot name.
    expect(sameIdentity(undefined, one)).toBe(true);
    expect(sameIdentity(one, undefined)).toBe(true);
    expect(sameIdentity(undefined, undefined)).toBe(true);
  });

  it("stops an opaque token that changed", () => {
    // No identity to read, so the credential itself is the comparison. A
    // rotation reads as a change and the write is dropped and REPORTED,
    // which is the wrong that can be seen.
    expect(sameIdentity(fingerprintOf("opaque-one"), fingerprintOf("opaque-two"))).toBe(
      false,
    );
    expect(sameIdentity(fingerprintOf("opaque-one"), fingerprintOf("opaque-one"))).toBe(
      true,
    );
  });

  it("does not let an opaque token match a JWT for the same person", () => {
    // Nothing links them, and guessing that they are the same person is the
    // guess that sends an edit to a stranger.
    expect(sameIdentity(fingerprintOf("one"), one)).toBe(false);
  });
});

describe("what a stranded write says", () => {
  it("carries the fields, so a caller can retire them from Saving…", () => {
    const error = new IdentityChanged(["hemoglobin", "platelets"]);
    expect(error.fields).toEqual(["hemoglobin", "platelets"]);
    expect(error.name).toBe("IdentityChanged");
    expect(error).toBeInstanceOf(Error);
  });

  it("names them in the message, because that is what reaches a console", () => {
    expect(new IdentityChanged(["hemoglobin"]).message).toContain("hemoglobin");
    expect(new IdentityChanged(["hemoglobin"]).message).toContain("account changed");
  });

  it("reads sensibly with no fields, which is the saved-filters case", () => {
    const error = new IdentityChanged();
    expect(error.fields).toEqual([]);
    expect(error.message).toContain("account changed");
  });
});
