// The bridge builds EXACT's per-user state adapter for hosts that cannot
// (HealthTree ONE is SvelteKit), so ONE gets the Registered and Favorites tabs.
// What matters is where that adapter writes and as whom, so this renders the
// bridge with TrialMatches replaced by a probe, over the real axios with a
// recording adapter — the interceptor timing is part of what is under test.
import { act, cleanup, render, waitFor } from "@testing-library/react";
import axios, { type AxiosAdapter, type InternalAxiosRequestConfig } from "axios";
import { StrictMode, useEffect, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TrialStateAdapter } from "./state";

interface Recorded {
  baseURL?: string;
  method?: string;
  url?: string;
  params?: Record<string, string>;
  authorization?: string;
  /** The body, so a write can be traced back to the adapter that made it —
   *  the credential alone cannot, since the new account writing its own row
   *  looks the same on the wire as the old adapter writing into it. */
  data?: unknown;
}

const requests: Recorded[] = [];
let meRow: Record<string, unknown> | null = { person_id: 9001, disease: "MM" };
const seen: { state?: TrialStateAdapter; patientInfo?: unknown }[] = [];
/** Errors from writes the probe made while unmounting. */
const unmountWriteErrors: string[] = [];
/** Stable number per adapter object, so a recorded body says which made it. */
const adapterIds = new Map<object, number>();

const respond = (config: InternalAxiosRequestConfig, data: unknown) => ({
  data,
  status: 200,
  statusText: "OK",
  headers: {},
  config,
});

const recordingAdapter: AxiosAdapter = async (config) => {
  requests.push({
    baseURL: config.baseURL,
    method: config.method,
    url: config.url,
    params: config.params,
    authorization: config.headers?.Authorization as string | undefined,
    data: typeof config.data === "string" ? JSON.parse(config.data) : config.data,
  });
  if (config.url === "/patient-info/me/") return respond(config, { patient_info: meRow });
  if (config.url === "/normalize-ctomop-row/") return respond(config, { diseaseCode: "MM" });
  if (config.url?.endsWith("/trial-enrollments/ids/")) {
    return respond(config, { trial_ids: ["7"], count: 1 });
  }
  return respond(config, {});
};
// Before the bridge is imported: `axios.create` copies the defaults.
axios.defaults.adapter = recordingAdapter;

vi.mock("./TrialMatches", () => ({
  default: (props: { state?: TrialStateAdapter; patientInfo?: unknown }) => {
    seen.push({ state: props.state, patientInfo: props.patientInfo });
    const { state } = props;
    // Which adapter this mount was given, stamped into the write below.
    if (state && !adapterIds.has(state)) adapterIds.set(state, adapterIds.size);
    const mountIndex = state ? adapterIds.get(state) : -1;
    // What TrialMatches really does on unmount: flush a pending write through
    // the adapter it was given (`hooks.ts`, the saved-filter writer).
    // BOTH halves, because they reach different services by different
    // clients and only one of them was ever guarded. Tagged so a test can
    // say which refused: an assertion that merely counts "session changed"
    // passes while one half leaks.
    useEffect(
      () => () => {
        // `?.()` on the methods too: one test hands the bridge a partial
        // state of its own, which is the shape a host is allowed to pass.
        state
          ?.setFavorite?.("7", true)
          .catch((error: Error) =>
            unmountWriteErrors.push(`favorite: ${error.message}`),
          );
        state
          ?.savePreferences?.({ phase: `from-adapter-${mountIndex}` })
          .catch((error: Error) =>
            unmountWriteErrors.push(`preferences: ${error.message}`),
          );
      },
      [state],
    );
    return <div data-testid="matches" />;
  },
}));

const { TrialMatchesBridgeRootForTests: Bridge } = await import("./TrialMatchesBridge");

const getToken = async () => "id-token";

beforeEach(() => {
  requests.length = 0;
  seen.length = 0;
  adapterIds.clear();
  unmountWriteErrors.length = 0;
  meRow = { person_id: 9001, disease: "MM" };
});

// Unmounting the probe fires a real write, which reaches the adapter a few
// microtasks later. Let it land here, not in the next test's request log.
afterEach(async () => {
  cleanup();
  await new Promise((resolve) => setTimeout(resolve, 20));
});

const lastSeen = () => seen[seen.length - 1];
const enrollmentRequests = () =>
  requests.filter((r) => r.url?.includes("trial-enrollments"));

describe("the bridge's per-user state", () => {
  it("is built for the resolved patient and writes to PRomop's v1 API as the caller", async () => {
    render(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={getToken}
        sessionKey="user-1"
      />,
    );
    await waitFor(() => expect(lastSeen()?.state).toBeDefined());
    expect(lastSeen().patientInfo).toEqual({ diseaseCode: "MM" });

    await expect(lastSeen().state!.listFavoriteIds()).resolves.toEqual(["7"]);
    expect(enrollmentRequests()[0]).toMatchObject({
      baseURL: "https://promop.example/api",
      url: "/v1/trial-enrollments/ids/",
      authorization: "Bearer id-token",
      params: { person_id: "9001", is_favorite: "true" },
    });
  });

  it("does not double v1 when the host mounted PRomop there", async () => {
    render(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        ctomopApiBasePath="/api/v1"
        getToken={getToken}
        sessionKey="user-1"
      />,
    );
    await waitFor(() => expect(lastSeen()?.state).toBeDefined());
    await lastSeen().state!.listFavoriteIds();
    expect(enrollmentRequests()[0]).toMatchObject({
      baseURL: "https://promop.example/api/v1",
      url: "/trial-enrollments/ids/",
    });
  });

  it("is absent when the resolved row has no person_id", async () => {
    meRow = { disease: "MM" };
    const { findByTestId } = render(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={getToken}
        sessionKey="user-1"
      />,
    );
    await findByTestId("matches");
    expect(lastSeen().state).toBeUndefined();
  });

  it("is absent without somewhere to write it", async () => {
    const { findByTestId } = render(
      <Bridge
        baseUrl="https://exact.example"
        getToken={getToken}
        patientInfo={{ diseaseCode: "MM" }}
        personId={9001}
      />,
    );
    await findByTestId("matches");
    expect(lastSeen().state).toBeUndefined();
  });

  it("uses the host's personId when the host owns the patient", async () => {
    render(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={getToken}
        patientInfo={{ diseaseCode: "MM" }}
        personId={42}
      />,
    );
    await waitFor(() => expect(lastSeen()?.state).toBeDefined());
    await lastSeen().state!.listFavoriteIds();
    expect(enrollmentRequests()[0].params).toMatchObject({ person_id: "42" });
  });

  it("ignores a host personId it cannot put in a path", async () => {
    const { findByTestId } = render(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={getToken}
        patientInfo={{ diseaseCode: "MM" }}
        personId={"12/../34" as unknown as number}
      />,
    );
    await findByTestId("matches");
    expect(lastSeen().state).toBeUndefined();
  });

  it("keeps a state the host passed", async () => {
    const own = { setFavorite: async () => {} } as unknown as TrialStateAdapter;
    render(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={getToken}
        sessionKey="user-1"
        state={own}
      />,
    );
    await waitFor(() => expect(lastSeen()?.patientInfo).toBeDefined());
    expect(lastSeen().state).toBe(own);
  });

  describe("across a change of signed-in user", () => {
    const tokenFor = (user: string) => async () => `token-${user}`;

    for (const keyed of [true, false]) {
      for (const strict of [false, true]) {
        it(`never sends the previous patient's unmount write under the next user's token (${
          keyed ? "sessionKey" : "getToken identity"
        }${strict ? ", StrictMode" : ""})`, async () => {
          const props = (user: string) => ({
            baseUrl: "https://exact.example",
            ctomopBaseUrl: "https://promop.example",
            getToken: tokenFor(user),
            ...(keyed ? { sessionKey: `user-${user}` } : {}),
          });
          const wrap = (el: ReactElement) => (strict ? <StrictMode>{el}</StrictMode> : el);

          const view = render(wrap(<Bridge {...props("1")} />));
          await view.findByTestId("matches");
          const first = lastSeen().state!;
          await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

          meRow = { person_id: 9002, disease: "MM" };
          // Under StrictMode the mount above already wrote once for 9001, as
          // user 1 — legitimately. Only what the switch sends is under test.
          requests.length = 0;
          await act(async () => {
            view.rerender(wrap(<Bridge {...props("2")} />));
          });
          await waitFor(() => expect(lastSeen()?.state).not.toBe(first));
          await view.findByTestId("matches");
          // Let every write the unmount started reach the adapter.
          await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

          const writes = enrollmentRequests().filter((r) => r.method !== "get");
          // Nothing for 9001 went out, under any token.
          expect(writes.filter((r) => r.params?.person_id === "9001")).toEqual([]);
          expect(unmountWriteErrors).toContain("favorite: [exact-remote] session changed");
    expect(unmountWriteErrors).toContain("preferences: [exact-remote] session changed");
          // Whatever did go out is the new user's own: StrictMode's double
          // effect on the new probe, and nothing without it.
          expect(writes).toHaveLength(strict ? 1 : 0);
          for (const write of writes) {
            expect(write).toMatchObject({
              params: { person_id: "9002" },
              authorization: "Bearer token-2",
            });
          }

          // And the new adapter works for the new user.
          requests.length = 0;
          await lastSeen().state!.listFavoriteIds();
          expect(enrollmentRequests()[0]).toMatchObject({
            params: { person_id: "9002" },
            authorization: "Bearer token-2",
          });
        });
      }
    }
  });

  it("refuses the old adapter when a host-owned patient changes with the session", async () => {
    // Here TrialMatches stays mounted and is handed a new adapter; the probe's
    // cleanup for the old one still runs, and must still be refused.
    const props = (user: string, personId: number) => ({
      baseUrl: "https://exact.example",
      ctomopBaseUrl: "https://promop.example",
      getToken: async () => `token-${user}`,
      sessionKey: `user-${user}`,
      patientInfo: { diseaseCode: "MM" },
      personId,
    });
    const view = render(<Bridge {...props("1", 42)} />);
    await waitFor(() => expect(lastSeen()?.state).toBeDefined());
    const first = lastSeen().state;
    requests.length = 0;

    await act(async () => {
      view.rerender(<Bridge {...props("2", 43)} />);
    });
    await waitFor(() => expect(lastSeen()?.state).not.toBe(first));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

    expect(enrollmentRequests()).toEqual([]);
    expect(unmountWriteErrors).toContain("favorite: [exact-remote] session changed");
    expect(unmountWriteErrors).toContain("preferences: [exact-remote] session changed");
  });

  for (const strict of [false, true]) {
    it(`refuses the unmount write when the host destroys the bridge on logout${
      strict ? " (StrictMode)" : ""
    }`, async () => {
      // A stable getToken over a global auth store: the shape where nothing
      // but the bridge's own lifecycle can tell the old write from a new user.
      let currentUser = "1";
      const fromStore = async () => `token-${currentUser}`;
      const el = (
        <Bridge
          baseUrl="https://exact.example"
          ctomopBaseUrl="https://promop.example"
          getToken={fromStore}
          sessionKey="user-1"
        />
      );
      const view = render(strict ? <StrictMode>{el}</StrictMode> : el);
      await view.findByTestId("matches");
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      requests.length = 0;
      unmountWriteErrors.length = 0;

      view.unmount();
      currentUser = "2"; // the next sign-in, before the flushed write is sent
      await new Promise((resolve) => setTimeout(resolve, 20));

      expect(enrollmentRequests()).toEqual([]);
      expect(unmountWriteErrors.sort()).toEqual([
        "favorite: [exact-remote] session changed",
        "preferences: [exact-remote] session changed",
      ]);
    });
  }

  for (const strict of [false, true]) {
    it(`refuses a flushed preference write when the host switches account in place${
      strict ? " (StrictMode)" : ""
    }`, async () => {
      // The case the hand-called test below does NOT reach, and the one that
      // actually happens: the host swaps accounts without unmounting, React
      // runs the deleted child's cleanup, and `useSavedFilters` flushes its
      // writer there by design — against the OLD adapter.
      //
      // A stable `getToken` over a global auth store is the shape
      // `types.ts` recommends and the shape that makes this dangerous:
      // nothing about the function identity says the user changed, so a
      // client built once keeps working and simply starts attaching the new
      // credential. EXACT keys the preferences row on that credential.
      //
      // A guard checked synchronously at call entry does not catch this —
      // measured — because React runs this cleanup BEFORE the parent's own
      // effect updates `sessionRef`. It has to sit where the token is
      // attached.
      let currentUser = "1";
      const fromStore = async () => `token-${currentUser}`;
      const props = (key: string) => ({
        baseUrl: "https://exact.example",
        ctomopBaseUrl: "https://promop.example",
        getToken: fromStore,
        sessionKey: key,
      });
      const el = <Bridge {...props("user-1")} />;
      const view = render(strict ? <StrictMode>{el}</StrictMode> : el);
      await view.findByTestId("matches");
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      requests.length = 0;
      unmountWriteErrors.length = 0;

      currentUser = "2";
      const next = <Bridge {...props("user-2")} />;
      view.rerender(strict ? <StrictMode>{next}</StrictMode> : next);
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

      // Nothing reached the NEW account's row. Asserted on the credential
      // rather than on "no write at all": under StrictMode the initial
      // double-mount flushes once within user 1's own session, which is a
      // correct write and not what this is about.
      // The OLD adapter's write, carrying the NEW account's credential.
      // Both halves of that matter: under StrictMode the new adapter also
      // writes with token-2, correctly, into its own row — so neither the
      // token nor the mere existence of a POST identifies the defect.
      const leaked = requests.filter(
        (r) =>
          r.url?.includes("user-state") &&
          r.method === "post" &&
          r.authorization === "Bearer token-2" &&
          (r.data as { preferences?: { phase?: string } })?.preferences?.phase ===
            "from-adapter-0",
      );
      expect(leaked).toEqual([]);
      expect(unmountWriteErrors).toContain(
        "preferences: [exact-remote] session changed",
      );
    });
  }

  it("refuses a preference write held over from the previous account", async () => {
    // EXACT keys the preferences row on the identity in the token, so an old
    // adapter reaching the NEW account's client writes one patient's filters
    // into another patient's record. And this is not hypothetical plumbing:
    // `useSavedFilters` flushes its writer during cleanup on purpose, and
    // the host switching accounts runs that cleanup against the OLD
    // transport — `hooks.ts` says so in as many words.
    //
    // The token reader beside it has been guarded since the PROMOP half was
    // written; this asserts the EXACT half is too, because the ref that lets
    // a token refresh through would otherwise let an account switch through
    // as well.
    const view = render(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={async () => "token-a"}
        sessionKey="user-1"
      />,
    );
    await view.findByTestId("matches");
    const stale = lastSeen().state!;
    requests.length = 0;

    view.rerender(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={async () => "token-b"}
        sessionKey="user-2"
      />,
    );
    await waitFor(() => expect(lastSeen().state).not.toBe(stale));

    await expect(stale.savePreferences({ phase: "II" })).rejects.toThrow(
      "session changed",
    );
    expect(requests.filter((r) => r.url?.includes("user-state"))).toEqual([]);
  });

  it("does not drop a write when only the token is refreshed within a session", async () => {
    const view = render(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={async () => "token-a"}
        sessionKey="user-1"
      />,
    );
    await view.findByTestId("matches");
    const adapter = lastSeen().state!;
    view.rerender(
      <Bridge
        baseUrl="https://exact.example"
        ctomopBaseUrl="https://promop.example"
        getToken={async () => "token-b"}
        sessionKey="user-1"
      />,
    );
    expect(lastSeen().state).toBe(adapter);
    requests.length = 0;
    await adapter.setFavorite("7", true);
    expect(enrollmentRequests()[0]).toMatchObject({
      method: "patch",
      params: { person_id: "9001" },
      authorization: "Bearer token-b",
    });
  });
});
