import { describe, expect, it } from "vitest";

import {
  hasUsableSessionKey,
  joinBaseUrl,
  nextSessionState,
  selectBridgeView,
  resolveSessionSignal,
  selectPatientInfo,
  shouldResolvePatient,
  type PatientLoad,
} from "./bridgeState";

describe("joinBaseUrl", () => {
  it("joins an origin and a path whichever way the host spells the slashes", () => {
    for (const [origin, path] of [
      ["https://exact.example", "/api"],
      ["https://exact.example/", "/api"],
      // Without normalisation this concatenated to "https://exact.exampleapi".
      ["https://exact.example", "api"],
      ["https://exact.example/", "api/"],
    ] as const) {
      expect(joinBaseUrl(origin, path)).toBe("https://exact.example/api");
    }
  });

  it("leaves the origin alone when there is no base path", () => {
    expect(joinBaseUrl("https://exact.example/", "")).toBe("https://exact.example");
  });

  it("keeps a multi-segment path", () => {
    expect(joinBaseUrl("https://promop.example", "/api/v1")).toBe(
      "https://promop.example/api/v1",
    );
  });
});

describe("selectPatientInfo", () => {
  const fetched = { diseaseCode: "MM" };
  const ready: PatientLoad = { status: "ready", patientInfo: fetched };

  it("uses what the bridge resolved when the host said nothing", () => {
    expect(selectPatientInfo(undefined, ready)).toBe(fetched);
  });

  it("lets an explicit prop win even after a patient has been resolved", () => {
    // The wrong-patient bug: the host switches profile (or starts
    // impersonating) and re-renders with an explicit payload, and the bridge
    // kept matching against whoever was loaded at mount time.
    const explicit = { diseaseCode: "FL" };
    expect(selectPatientInfo(explicit, ready)).toBe(explicit);
  });

  it("treats an explicit null as an answer, not as 'ask again'", () => {
    expect(selectPatientInfo(null, ready)).toBeNull();
  });

  it("passes nothing through while the resolution is still in flight", () => {
    expect(selectPatientInfo(undefined, { status: "loading" })).toBeUndefined();
    expect(selectPatientInfo(undefined, { status: "error" })).toBeUndefined();
    expect(selectPatientInfo(undefined, { status: "idle" })).toBeUndefined();
  });

  it("surfaces a resolved-but-empty profile as null, not as 'nothing known'", () => {
    expect(
      selectPatientInfo(undefined, { status: "ready", patientInfo: null }),
    ).toBeNull();
  });
});

describe("shouldResolvePatient", () => {
  it("resolves only when the host gave somewhere to ask and no answer", () => {
    expect(shouldResolvePatient({ ctomopBaseUrl: "https://promop.example" })).toBe(true);
  });

  it("does not resolve without a PRomop origin", () => {
    expect(shouldResolvePatient({})).toBe(false);
  });

  it("does not second-guess a host that supplied the patient", () => {
    const ctomopBaseUrl = "https://promop.example";
    expect(shouldResolvePatient({ ctomopBaseUrl, patientInfo: { a: 1 } })).toBe(false);
    expect(shouldResolvePatient({ ctomopBaseUrl, patientInfo: null })).toBe(false);
    expect(shouldResolvePatient({ ctomopBaseUrl, personId: 9009 })).toBe(false);
  });

  it("treats a null personId as no answer and still resolves", () => {
    // `personId: pid ?? null` is the idiomatic spelling in a host that has no
    // types to stop it, and there is no useful difference between "no person
    // id" and "a null person id".
    expect(shouldResolvePatient({ ctomopBaseUrl: "https://promop.example", personId: null })).toBe(
      true,
    );
  });

  it("treats personId 0 as an answer, however unlikely a value it is", () => {
    expect(shouldResolvePatient({ ctomopBaseUrl: "https://promop.example", personId: 0 })).toBe(
      false,
    );
  });
});

describe("selectBridgeView", () => {
  const view = (over: Partial<Parameters<typeof selectBridgeView>[0]> = {}) =>
    selectBridgeView({
      shouldLoad: false,
      load: { status: "idle" },
      patientInfo: undefined,
      ...over,
    });

  it("shows the spinner while resolving", () => {
    expect(view({ load: { status: "loading" } })).toBe("loading");
  });

  it("shows the spinner in the gap before the effect starts resolving", () => {
    // The render between the host dropping its own patient and the effect
    // firing. Anything but "loading" flashes the developer notice for a frame.
    expect(view({ shouldLoad: true, load: { status: "idle" } })).toBe("loading");
  });

  it("shows the error card, not a half-rendered match list", () => {
    expect(view({ load: { status: "error", httpStatus: 401 } })).toBe("error");
  });

  it("gives a real empty state to a patient with no profile", () => {
    // Both routes to the same answer: the bridge resolved an empty row, and
    // the host said so outright.
    expect(view({ shouldLoad: true, load: { status: "ready", patientInfo: null }, patientInfo: null })).toBe(
      "no-profile",
    );
    expect(view({ patientInfo: null })).toBe("no-profile");
  });

  it("keeps the developer notice for a host that wired nothing up", () => {
    // `undefined` is nobody answering, which is a wiring mistake and should
    // stay loud rather than being dressed up as an empty profile.
    expect(view({ patientInfo: undefined })).toBe("matches");
  });

  it("does not claim 'no profile' when a personId is in play", () => {
    expect(view({ patientInfo: null, personId: 9009 })).toBe("matches");
  });

  it("treats a null personId as no personId", () => {
    // Non-TypeScript hosts are the point of this bridge, and `pid ?? null` is
    // how they spell it.
    expect(view({ patientInfo: null, personId: null })).toBe("no-profile");
  });

  it("puts the error card ahead of the empty-profile screen", () => {
    // Pins the branch order: a failed resolution must not be dressed up as a
    // patient who simply has no profile.
    expect(view({ load: { status: "error" }, patientInfo: null })).toBe("error");
  });

  it("passes through to the matches once a patient is resolved", () => {
    expect(
      view({ shouldLoad: true, load: { status: "ready", patientInfo: { a: 1 } }, patientInfo: { a: 1 } }),
    ).toBe("matches");
  });
});

describe("nextSessionState", () => {
  const prev = { key: "true|https://e||https://p|/api", signal: "user-1" };

  it("keeps what it has when nothing moved", () => {
    expect(
      nextSessionState(prev, { routeKey: prev.key, sessionSignal: "user-1", shouldLoad: true }),
    ).toBeNull();
  });

  it("drops the resolved patient when the signed-in user changes", () => {
    // The whole point: user-2 must never render against user-1's profile.
    const next = nextSessionState(prev, {
      routeKey: prev.key,
      sessionSignal: "user-2",
      shouldLoad: true,
    });
    expect(next).toEqual({
      session: { key: prev.key, signal: "user-2" },
      load: { status: "loading" },
    });
  });

  it("compares the signal by identity, so a getToken fallback works", () => {
    const a = () => "t";
    const b = () => "t";
    expect(
      nextSessionState({ key: prev.key, signal: a }, {
        routeKey: prev.key,
        sessionSignal: b,
        shouldLoad: true,
      }),
    ).not.toBeNull();
    expect(
      nextSessionState({ key: prev.key, signal: a }, {
        routeKey: prev.key,
        sessionSignal: a,
        shouldLoad: true,
      }),
    ).toBeNull();
  });

  it("drops it when the service it was fetched from changes", () => {
    expect(
      nextSessionState(prev, {
        routeKey: "true|https://other||https://p|/api",
        sessionSignal: "user-1",
        shouldLoad: true,
      }),
    ).not.toBeNull();
  });

  it("goes idle rather than to a spinner when there is nothing to resolve", () => {
    // The host now supplies the patient itself, so a spinner would never end.
    expect(
      nextSessionState(prev, { routeKey: "false|x", sessionSignal: "user-1", shouldLoad: false }),
    ).toEqual({
      session: { key: "false|x", signal: "user-1" },
      load: { status: "idle" },
    });
  });
});

describe("resolveSessionSignal", () => {
  const getToken = () => "tok";

  it("uses the session key the host gave it", () => {
    expect(resolveSessionSignal("user-1", getToken)).toBe("user-1");
    // 0 is a real id, unlike the spellings below.
    expect(resolveSessionSignal(0, getToken)).toBe(0);
  });

  it("falls back to getToken for every spelling of 'I do not have one'", () => {
    // Each of these, taken literally, would pin the signal to one value for
    // every user and silently disable the session guard.
    // `false` is `auth.ready && auth.userId` before auth is ready — and the
    // quietest of the lot, because a stuck signal never thrashes and so never
    // trips the warning either.
    for (const key of [undefined, null, "", false, true] as const) {
      expect(resolveSessionSignal(key, getToken)).toBe(getToken);
      expect(hasUsableSessionKey(key)).toBe(false);
    }
  });

  it("falls back for NaN, which would otherwise reset on every render", () => {
    // `Number(sub)` on a non-numeric id. NaN never equals itself, so it is not
    // a stuck signal but a permanently changing one: React's "Too many
    // re-renders", i.e. the remote's subtree dying inside the host page.
    expect(resolveSessionSignal(Number.NaN, getToken)).toBe(getToken);
    expect(hasUsableSessionKey(Number.NaN)).toBe(false);
  });

  it("reports a usable key as usable", () => {
    expect(hasUsableSessionKey("user-1")).toBe(true);
    expect(hasUsableSessionKey(0)).toBe(true);
  });
});

describe("nextSessionState under a NaN signal", () => {
  it("does not reset forever when the signal is NaN", () => {
    // Defence in depth: `resolveSessionSignal` keeps NaN out, and `Object.is`
    // means it would still settle if one arrived another way.
    const prev = { key: "k", signal: Number.NaN };
    expect(
      nextSessionState(prev, { routeKey: "k", sessionSignal: Number.NaN, shouldLoad: true }),
    ).toBeNull();
  });
});
