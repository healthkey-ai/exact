// The real bridge and the REAL TrialMatches, with the host owning the
// patient — the only arrangement in which the defect this guards is
// reachable, and the one every earlier attempt at this test mocked away.
//
// Three reviews found the same bug at three call sites, and each time the
// regression test mocked `./TrialMatches` and re-implemented the one line
// under test inside the mock. Measured: with the component mocked, reverting
// the fix in `TrialMatches.tsx` left the entire suite green. So this file
// drives the component. It costs about a second and a half.
//
// The rule, stated once in `hooks.ts` on `live()`: a queued payload belongs
// to the reader who produced it, and must be dropped rather than re-routed
// if that reader is no longer the one the write will be attributed to.
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axios, { type AxiosAdapter, type InternalAxiosRequestConfig } from "axios";
import { beforeEach, describe, expect, it } from "vitest";

const requests: { url?: string; method?: string; authorization?: string; data?: unknown }[] = [];
const respond = (config: InternalAxiosRequestConfig, data: unknown) => ({
  data, status: 200, statusText: "OK", headers: {}, config,
});
const recordingAdapter: AxiosAdapter = async (config) => {
  requests.push({
    url: config.url, method: config.method,
    authorization: config.headers?.Authorization as string | undefined,
    data: typeof config.data === "string" ? JSON.parse(config.data) : config.data,
  });
  if (config.url === "/patient-info/me/") return respond(config, { patient_info: { person_id: 42 } });
  if (config.url === "/normalize-ctomop-row/") return respond(config, { diseaseCode: "MM" });
  if (config.url?.endsWith("/trial-enrollments/ids/")) return respond(config, { trial_ids: [], count: 0 });
  if (config.url?.includes("trial-search-preferences")) return respond(config, { preferences: {} });
  if (config.url?.includes("form-settings")) return respond(config, {});
  return respond(config, { count: 0, results: [] });
};
axios.defaults.adapter = recordingAdapter;

const { TrialMatchesBridgeRootForTests: Bridge } = await import("./TrialMatchesBridge");

let user = "1";
const fromStore = async () => `token-${user}`;
const base = () => ({
  baseUrl: "https://exact.example",
  ctomopBaseUrl: "https://promop.example",
  getToken: fromStore,
  patientInfo: { diseaseCode: "MM" },
  personId: 42,
});
const leaked = () => requests.filter((r) =>
  r.url?.includes("trial-search-preferences") && r.method === "post" && r.authorization === "Bearer token-2");

describe("a write never crosses an account switch, through the real tree", () => {
  beforeEach(() => {
    user = "1";
    requests.length = 0;
  });

  const openFilters = async () => {
    await waitFor(
      () =>
        expect(
          screen.queryByRole("button", { name: /Filter Results|Filters \(/ }),
        ).not.toBeNull(),
      { timeout: 3000 },
    );
    // Idempotent: the toggle closes an open panel, and after an account
    // switch the panel is often still open.
    if (screen.queryByLabelText("Sponsor")) return;
    await userEvent.click(
      screen.getByRole("button", { name: /Filter Results|Filters \(/ }),
    );
    await screen.findByLabelText("Sponsor");
  };

  it("does not post user 1's typed filter under user 2's token", async () => {
    const view = render(<Bridge {...base()} sessionKey="user-1" />);
    await openFilters();
    const sponsor = await screen.findByLabelText("Sponsor");
    await userEvent.type(sponsor, "USER1SPONSOR");
    requests.length = 0;

    user = "2";
    await act(async () => {
      view.rerender(<Bridge {...base()} sessionKey="user-2" />);
    });
    await act(() => new Promise((r) => setTimeout(r, 1500)));

    expect(leaked()).toEqual([]);
  });

  it("does not carry user 1's weights into user 2's first save", async () => {
    // Not a key: a RETENTION rule. `ownedFields` keeps the weights across a
    // switch on purpose — they are the reader's, not the patient's — which
    // is right for a patient change and exactly wrong for an account one.
    // Measured before the fix: user 2's first keystroke sent user 1's four
    // weights alongside their own filter.
    const view = render(<Bridge {...base()} sessionKey="user-1" />);
    await openFilters();
    await userEvent.click(
      await screen.findByRole("button", { name: /Suitability Preferences/ }),
    );
    const risk = await screen.findByLabelText("Risk Weight");
    await userEvent.clear(risk);
    await userEvent.type(risk, "90");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await act(() => new Promise((r) => setTimeout(r, 800)));

    user = "2";
    await act(async () => {
      view.rerender(<Bridge {...base()} sessionKey="user-2" />);
    });
    requests.length = 0;
    await openFilters();
    const sponsor = await screen.findByLabelText("Sponsor");
    await userEvent.type(sponsor, "USER2SPONSOR");
    await act(() => new Promise((r) => setTimeout(r, 1500)));

    const sent = requests.filter(
      (r) => r.url?.includes("trial-search-preferences") && r.method === "post",
    );
    for (const write of sent) {
      const body = (write.data as { preferences?: Record<string, unknown> })
        ?.preferences;
      expect(body?.riskWeight).toBeUndefined();
    }
  });

  it("honours a numeric stateIdentity, which is how a person id arrives", async () => {
    // WHAT THIS ASSERTS, and what it deliberately does not. A numeric
    // identity was being dropped by a string-only check, so the key did not
    // move at all and NOTHING re-keyed. Honoured, the key moves and every
    // key-driven guard works: the bookmarks below are re-read under the new
    // credential instead of being served from the previous account's cache.
    //
    // The WRITE is a different matter and is not asserted here, because it
    // would fail: a key chooses which adapter a flush goes through, and the
    // only thing that refuses is the token reader, which fires on the
    // SESSION SIGNAL. An identity that moves without it is exact#583. The
    // first draft of this test asserted the write and failed — which is the
    // issue reproducing itself, not a defect in this fix.

    // The guard was being disabled by a host doing the obvious thing:
    // PROMOP's `person_id` is an integer, so `stateIdentity={user.id}` is
    // the natural spelling, and a string-only check dropped it and fell
    // back to a session signal that does not move for a host with a stable
    // `getToken` and no `sessionKey`. Through the real tree, because the
    // helper's own unit test cannot see whether the bridge asks it.
    const props = () => ({
      baseUrl: "https://exact.example",
      ctomopBaseUrl: "https://promop.example",
      getToken: fromStore,
      patientInfo: { diseaseCode: "MM" },
      personId: 42,
    });
    const view = render(<Bridge {...props()} stateIdentity={1} />);
    await waitFor(() =>
      expect(
        requests.filter((r) => r.url?.endsWith("/trial-enrollments/ids/")).length,
      ).toBeGreaterThan(0),
    );
    requests.length = 0;

    user = "2";
    await act(async () => {
      view.rerender(<Bridge {...props()} stateIdentity={2} />);
    });
    await act(() => new Promise((r) => setTimeout(r, 200)));

    const reread = requests.filter((r) => r.url?.endsWith("/trial-enrollments/ids/"));
    expect(reread.length).toBeGreaterThan(0);
    expect(reread.every((r) => r.authorization === "Bearer token-2")).toBe(true);
  });

  it("does not move the stored filter namespace, whatever the identity is", async () => {
    // The localStorage path: no adapter at all, so filters live on disk.
    // The namespace must not depend on the identity — a generated one is per
    // page load and would forget the reader's filters every visit, and a
    // host-supplied one would move the namespace once, losing them on the
    // first load after deploy and orphaning the old entry.
    //
    // `auth0|5f3c9b` specifically. That is the usual spelling of `sub`, and
    // a persisted key derived by slicing `stateKey` to its first separator
    // kept everything after the pipe — so exactly the hosts that name
    // themselves still had their namespace moved. Pinned here rather than
    // only on the helper, because the helper was right and the CALLER was
    // the bug.
    const local = () => ({
      baseUrl: "https://exact.example",
      getToken: fromStore,
      patientInfo: { diseaseCode: "MM" },
      personId: 42,
    });
    const keysAfter = async (identity?: string) => {
      localStorage.clear();
      const view = render(
        identity === undefined ? (
          <Bridge {...local()} />
        ) : (
          <Bridge {...local()} stateIdentity={identity} />
        ),
      );
      await openFilters();
      await userEvent.type(await screen.findByLabelText("Sponsor"), "X");
      await act(() => new Promise((r) => setTimeout(r, 800)));
      const keys = Object.keys(localStorage).filter((k) => k.startsWith("exact.filters."));
      view.unmount();
      return keys;
    };

    const plain = await keysAfter();
    const auth0 = await keysAfter("auth0|5f3c9b");
    const named = await keysAfter("k:user-1");

    expect(plain.length).toBe(1);
    expect(auth0).toEqual(plain);
    expect(named).toEqual(plain);
  });

  it("does not read user 1's bookmarks for user 2", async () => {
    // Not a write, and it was leaking too: `useStateIds` caches on the same
    // key with a 30s staleTime, so before the identity was in it, user 2 saw
    // user 1's bookmarked and registered trials for half a minute. Nothing
    // guarded this; it was fixed as a side effect and is pinned here.
    const view = render(<Bridge {...base()} sessionKey="user-1" />);
    await waitFor(() =>
      expect(
        requests.filter((r) => r.url?.endsWith("/trial-enrollments/ids/")).length,
      ).toBeGreaterThan(0),
    );
    requests.length = 0;

    user = "2";
    await act(async () => {
      view.rerender(<Bridge {...base()} sessionKey="user-2" />);
    });
    await act(() => new Promise((r) => setTimeout(r, 200)));

    const reread = requests.filter((r) => r.url?.endsWith("/trial-enrollments/ids/"));
    expect(reread.length).toBeGreaterThan(0);
    expect(reread.every((r) => r.authorization === "Bearer token-2")).toBe(true);
  });
});
