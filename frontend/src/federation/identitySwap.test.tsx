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
  default: (props: { state?: TrialStateAdapter; stateIdentity?: string }) => {
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

  let user = "1";
  const fromStore = async () => `token-${user}`;
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

  // The third shape is NOT fixed, and saying so is the point of this test
  // rather than leaving it to be rediscovered.
  //
  // A host that swaps the credential in a global store and signals nothing —
  // no new `sessionKey`, no new `getToken`, no re-render — is already outside
  // the contract, and its documented consequence was "a mount reused across a
  // logout/login keeps showing the previous user's matches". Since EXACT
  // began keying rows on the token, the consequence is worse: the previous
  // user's filters are written into the new user's row, and nothing on either
  // side can tell. No key can catch it — React never learns anything changed
  // — so closing it means either the host honouring the contract or EXACT
  // verifying an asserted identity on the write and refusing a mismatch.
  it.skip("cannot yet be dropped when the host signals nothing at all", async () => {
    const view = await start({ sessionKey: "user-1" });

    act(() => {
      persist!({ phase: "USER-1-FILTERS" });
    });
    user = "2"; // no rerender, no new key, no new getToken
    await act(() => new Promise((r) => setTimeout(r, 800)));

    expect(leaked()).toEqual([]);
    view.unmount();
  });
});
