// Regression for the finding below: a queued saved-filter write must never
// land in the row of an account that did not make it. Host owns the patient
// (patientInfo + personId), so the patient key never changes and
// TrialMatches is never unmounted — which is what routes the flush through
// `live()` to the NEW adapter instead of the captured one.
import { act, render } from "@testing-library/react";
import axios, { type AxiosAdapter, type InternalAxiosRequestConfig } from "axios";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TrialStateAdapter } from "./state";
import { stateKeyOf } from "./bridgeState";
import { useSavedFilters } from "./hooks";

const requests: { url?: string; method?: string; authorization?: string; data?: unknown }[] = [];
const respond = (config: InternalAxiosRequestConfig, data: unknown) => ({
  data, status: 200, statusText: "OK", headers: {}, config,
});
const recordingAdapter: AxiosAdapter = async (config) => {
  requests.push({
    url: config.url,
    method: config.method,
    authorization: config.headers?.Authorization as string | undefined,
    data: typeof config.data === "string" ? JSON.parse(config.data) : config.data,
  });
  if (config.url === "/patient-info/me/") return respond(config, { patient_info: { person_id: 42 } });
  if (config.url === "/normalize-ctomop-row/") return respond(config, { diseaseCode: "MM" });
  if (config.url?.endsWith("/trial-enrollments/ids/")) return respond(config, { trial_ids: [], count: 0 });
  return respond(config, {});
};
axios.defaults.adapter = recordingAdapter;

let persist: ((f: Record<string, unknown>) => void) | null = null;

vi.mock("./TrialMatches", () => ({
  default: (props: {
    state?: TrialStateAdapter;
    stateIdentity?: string;
    credentialIdentity?: () => string | undefined;
    credentialIdentityNow?: () => Promise<string | undefined> | string | undefined;
  }) => {
    // The key the REAL component builds: identity first, then the patient.
    // A probe that hardcodes a patient-only key models the component as it
    // was before the identity was part of it, and cannot see a fix that
    // works by changing the key — which is what the first version of this
    // regression test did.
    // The component's OWN key rule, not a copy of it: a probe that rebuilds
    // the key is testing its own arithmetic, and the first version of this
    // passed happily with the identity removed from the real component.
    const saved = useSavedFilters(
      props.state,
      stateKeyOf(props.stateIdentity, 42, "host"),
      () => undefined,
      "state",
      undefined,
      // Threaded because the real component threads it, and the shapes below
      // exist precisely because the KEY above cannot see them. A probe that
      // dropped this would test the key alone and go on reporting the leak.
      props.credentialIdentity,
      props.credentialIdentityNow,
    );
    useEffect(() => { persist = saved.persist as never; }, [saved]);
    return <div data-testid="matches" />;
  },
}));

const { TrialMatchesBridgeRootForTests: Bridge } = await import("./TrialMatchesBridge");

describe("a filter edit never crosses an account switch", () => {
  const start = async (overrides: Record<string, unknown> = {}) => {
    const view = render(<Bridge {...base()} {...overrides} />);
    await view.findByTestId("matches");
    await act(() => new Promise((r) => setTimeout(r, 400)));
    requests.length = 0;
    return view;
  };

  // `null` is a real state, not a test contrivance: `getToken: () =>
  // auth.currentUser?.getIdToken()` answers nothing for the whole interval
  // between a sign-out and the next sign-in.
  let user: string | null = "1";
  const fromStore = async () => (user === null ? undefined : `token-${user}`);
  const base = () => ({
    baseUrl: "https://exact.example",
    ctomopBaseUrl: "https://promop.example",
    getToken: fromStore,
    patientInfo: { diseaseCode: "MM" },
    personId: 42,
  });
  const leaked = () =>
    requests.filter(
      (r) =>
        r.url?.includes("user-state") &&
        r.method === "post" &&
        r.authorization === "Bearer token-2",
    );

  beforeEach(() => {
    user = "1";
    requests.length = 0;
  });

  // The two ways the host contract says an account change is signalled
  // (`types.ts`, `sessionKey` and `getToken`). Either must be enough: the
  // debounced write below was made by user 1 and must never reach user 2's
  // row, which EXACT picks from the token.
  it("is dropped when the host changes sessionKey", async () => {
    const view = await start({ sessionKey: "user-1" });

    act(() => {
      persist!({ phase: "USER-1-FILTERS" });
    });
    user = "2";
    await act(async () => {
      view.rerender(<Bridge {...base()} sessionKey="user-2" />);
    });
    await act(() => new Promise((r) => setTimeout(r, 800)));

    expect(leaked()).toEqual([]);
  });

  it("is dropped when the host hands over a new getToken", async () => {
    // The other supported signal, for a host with no session id to pass.
    // `types.ts`: "do hand over a new one when the signed-in user changes".
    const view = await start({});

    act(() => {
      persist!({ phase: "USER-1-FILTERS" });
    });
    user = "2";
    await act(async () => {
      view.rerender(
        <Bridge {...base()} getToken={async () => `token-${user}`} />,
      );
    });
    await act(() => new Promise((r) => setTimeout(r, 800)));

    expect(leaked()).toEqual([]);
  });

  // THE SHAPES NO KEY COULD CATCH, and what closed them.
  //
  // Everything above is a host signalling an account change the way
  // `types.ts` asks — a new `sessionKey`, or a new `getToken` — and a KEY is
  // enough for those: it selects which adapter the flush goes through.
  //
  // Below are hosts whose account change never reaches
  // `resolveSessionSignal` as a new, usable signal. Nothing in the React
  // tree learns anything changed, so there is nothing for a key to be
  // derived from, and five key-shaped fixes each closed a real case and
  // none of them could close these. They were measured, not guessed
  // (#583), and their consequence stopped being a stale read when EXACT
  // began keying rows on the token: it is a write into a stranger's row.
  //
  // What closes them is not a key. The queues capture the credential's
  // fingerprint when a write is ENQUEUED and read it afresh when the write
  // is SENT, and drop the payload if the two disagree
  // (`identityFingerprint.ts`, `patientWriter.ts`, `preferences.ts`). It
  // needs no host cooperation and no server change.
  //
  // BOTH READINGS MATTER. The cached one alone leaves the hole wide open:
  // it only moves when somebody fetches a token, and a whole debounce
  // window can pass with no request in it — so the swap is invisible, the
  // batch passes, and the transport's interceptor then sends it under the
  // new credential. Ablating the fresh read fails the first test below,
  // measured. The remaining race is microseconds between the check and the
  // interceptor's own fetch, against a debounce of a quarter of a second.
  //
  // Not covered here: two hosts whose session keys collide. That one is
  // closed by the same mechanism — the credentials differ — but it needs a
  // second bridge to model and the shapes below already exercise the
  // mechanism.
  it("is dropped when the host signals nothing at all", async () => {
    const view = await start({ sessionKey: "user-1" });

    act(() => {
      persist!({ phase: "USER-1-FILTERS" });
    });
    user = "2"; // no rerender, no new key, no new getToken
    await act(() => new Promise((r) => setTimeout(r, 800)));

    expect(leaked()).toEqual([]);
    view.unmount();
  });

  it("is dropped when only the host's stateIdentity moves", async () => {
    // Shape 1, and the only one of these a reasonable host does: it names
    // the identity but has no `sessionKey` and a stable `getToken`. The READ
    // side re-keys correctly, which is what made this so easy to miss — the
    // bookmarks come back under the new account while the queued write is
    // still the old one's.
    const view = await start({ stateIdentity: "user-1" });

    act(() => {
      persist!({ phase: "USER-1-FILTERS" });
    });
    user = "2";
    await act(async () => {
      view.rerender(<Bridge {...base()} stateIdentity="user-2" />);
    });
    await act(() => new Promise((r) => setTimeout(r, 800)));

    expect(leaked()).toEqual([]);
    view.unmount();
  });

  it("is dropped under a sessionKey the resolver refuses", async () => {
    // Shape 3. `resolveSessionSignal` refuses `""` and `NaN` and falls back
    // to `getToken`, documenting that these "silently disable the guard" —
    // so a host passing an empty string has no session signal at all, for
    // the whole mount, however many times it re-renders with a new one.
    //
    // `""` rather than the ticket's `false` to `true`: `sessionKey` is typed
    // `string | number | null | undefined`, so a boolean cannot reach here
    // from a typed host, and a test that cast one would be measuring a
    // shape the contract already prevents.
    const view = await start({ sessionKey: "" });

    act(() => {
      persist!({ phase: "USER-1-FILTERS" });
    });
    user = "2";
    await act(async () => {
      view.rerender(<Bridge {...base()} sessionKey="" />);
    });
    await act(() => new Promise((r) => setTimeout(r, 800)));

    expect(leaked()).toEqual([]);
    view.unmount();
  });

  it("is dropped when the edit was made while nobody was signed in", async () => {
    // The hole the FIRST version of this guard left, and the reason a
    // "no credential" answer is not recorded as "unknown". Signed out, every
    // token read answers nothing; recorded as unknown that downgraded the
    // cache, `sameIdentity` waved the next capture through, and the edit went
    // out under whoever signed in during the debounce. Measured through this
    // same bridge before the fix: the POST carried `Bearer token-2`.
    const view = await start({ sessionKey: "user-1" });

    user = null; // the host signs A out
    // A write that actually GOES, so a token is read while signed out and
    // the cache records that answer. Without letting this one flush there is
    // no read, the cache still holds A, and the test passes for the wrong
    // reason — measured: it survived the ablation until this wait was added.
    act(() => {
      persist!({ phase: "WARM" });
    });
    await act(() => new Promise((r) => setTimeout(r, 800)));

    // Now the edit that matters, captured against whatever that read left
    // behind.
    act(() => {
      persist!({ phase: "USER-1-FILTERS" });
    });
    user = "2"; // B signs in during the debounce
    await act(() => new Promise((r) => setTimeout(r, 800)));

    expect(leaked()).toEqual([]);
    view.unmount();
  });

  it("does not carry one reader's saved filters into the next reader's row", async () => {
    // #603, and a different mechanism from everything above. The write the
    // new reader makes IS theirs — captured under their credential and sent
    // under it — so #583's guard has nothing to object to. What crosses is
    // the MERGE BASE: `adapterPreferences` caches what the server holds so a
    // partial payload does not replace the row, and that cache belongs to
    // whoever it was read for.
    //
    // Through the bridge rather than in a unit test, because the unit tests
    // can only show the transport forgetting; this shows what actually goes
    // on the wire.
    const view = await start({ sessionKey: "user-1" });

    act(() => {
      persist!({ searchTitle: "USER-1-ONLY" });
    });
    await act(() => new Promise((r) => setTimeout(r, 800)));

    user = "2"; // no rerender, no new key, no new getToken

    // TWO edits after the swap, and the first one is scaffolding. #583
    // strands it — the cached fingerprint still says user one at the moment
    // it is queued — but its send-time check fetches a token and so brings
    // the cache up to date. Only the second edit is both queued and sent as
    // user two, which is the shape #603 is about: a write nothing else
    // objects to, carrying the previous reader's row underneath it.
    act(() => {
      persist!({ distance: 50 });
    });
    await act(() => new Promise((r) => setTimeout(r, 800)));
    const before = requests.length;

    act(() => {
      persist!({ distance: 100 });
    });
    await act(() => new Promise((r) => setTimeout(r, 800)));

    const bodies = requests
      .slice(before)
      .filter((r) => r.url?.includes("user-state") && r.method === "post")
      .map((r) => JSON.stringify(r.data ?? {}));
    expect(bodies.length).toBeGreaterThan(0);
    for (const body of bodies) {
      expect(body).not.toContain("USER-1-ONLY");
    }
    view.unmount();
  });

  it("still lets the same reader save across a token REFRESH", async () => {
    // The cost of getting this wrong in the other direction. Tokens rotate —
    // Firebase roughly hourly — and a guard comparing credentials as strings
    // would drop every edit made across the hour.
    //
    // Real JWTs with the same `sub` and different `iat`, because the opaque
    // `token-1` this file uses elsewhere never changes and a rotation test
    // over it tests nothing: measured, with `fingerprintOf` reduced to
    // comparing whole strings, the earlier version of this test still
    // passed.
    const jwtFor = (iat: number) => {
      const seg = (value: unknown) =>
        btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      return `${seg({ alg: "RS256" })}.${seg({ iss: "https://issuer.example", sub: "one", iat })}.sig`;
    };
    let issued = 1000;
    const rotating = async () => jwtFor(issued);
    const view = render(<Bridge {...base()} getToken={rotating} sessionKey="user-1" />);
    await view.findByTestId("matches");
    await act(() => new Promise((r) => setTimeout(r, 400)));
    requests.length = 0;

    act(() => {
      persist!({ phase: "USER-1-FILTERS" });
    });
    issued = 9000; // the same person, an hour later
    await act(() => new Promise((r) => setTimeout(r, 800)));

    const writes = requests.filter(
      (r) => r.url?.includes("user-state") && r.method === "post",
    );
    expect(writes.length).toBeGreaterThan(0);
    view.unmount();
  });
});
