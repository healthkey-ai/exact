/**
 * Framework-agnostic mount for TrialMatches.
 *
 * `./TrialMatches` (the plain React component) is unchanged, for React hosts
 * such as ht-phr. This entry serves hosts that are not React — HealthTree ONE
 * is SvelteKit — and exposes the provider contract:
 *
 *     const { default: provider } = await loadRemote("exact_remote/TrialMatchesBridge");
 *     await provider().render({ dom, baseUrl, getToken, ... });
 *     provider().destroy({ moduleName, dom });
 *
 * The two `provider()` calls above must reach the same instance, and with
 * bridge-react's own factory they do not: `createBridgeComponent` returns
 * `() => { const rootMap = new Map(); ... }`, so each call gets a private map,
 * and `destroy` — which is `rootMap.get(dom)` plus a no-op when absent —
 * unmounts nothing. The React tree, its `popstate` listener and its
 * QueryClient survive, and the next mount calls `createRoot()` on a container
 * that already has a live root. We memoise the factory below so `provider()`
 * is idempotent and the contract above holds as written.
 *
 * Props are data-only: the host passes `baseUrl` + `getToken` instead of a live
 * AxiosInstance, so it needs neither axios nor react-query, and EXACT keeps
 * ownership of how it calls its own API.
 * See ht-phr/docs/mf-bridge-proposal.md §6.2.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import axios, { type AxiosInstance } from "axios";
import { createBridgeComponent } from "@module-federation/bridge-react/v19";

import TrialMatches from "./TrialMatches";
import { normalizeCtomopRow } from "./api";
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
import type { PatientInfo, TrialMatchesBridgeProps } from "./types";

export type { TrialMatchesBridgeProps };

/** Bounds the bridge's own patient resolution, which blocks the whole screen:
 *  without it a hung PRomop leaves the loading state up forever, with no retry
 *  and nothing for the user to do. Matches the dev clients
 *  (`src/dev/exactAuth.ts`, `src/dev/ctomopClient.ts`). */
export const RESOLVE_TIMEOUT_MS = 10_000;

/** The client handed to TrialMatches gets no timeout, which is what a React
 *  host building its own client has today.
 *
 *  Note this diverges from the dev harness, whose `makeExactClient`
 *  (`src/dev/exactAuth.ts:76`) does set 10s on the same endpoint.
 *
 *  It runs `/trials/` — a matcher call over the whole corpus, behind a service
 *  that cold-starts — and TrialMatches' QueryClient is built with no
 *  `defaultOptions`, so react-query's default three retries with backoff
 *  apply. Any per-attempt ceiling is therefore multiplied by four before the
 *  user sees anything: a 60s budget turns a slow-but-fine search into a
 *  four-minute failure. Bounding that belongs with whoever owns the retry
 *  policy, not with a timeout invented here. */
export const SEARCH_TIMEOUT_MS = undefined;

type TokenReader = () => Promise<string | null | undefined>;

/* Both of the misconfigurations below are silent: the screen looks like a
 * legitimate state, so nothing tells the host developer they wired it wrong.
 * Once per page is enough to be findable without becoming noise. */
const warned = new Set<string>();
function warnOnce(key: string, message: string): void {
  if (warned.has(key)) return;
  warned.add(key);
  console.warn(`[exact-remote] ${message}`);
}

/** Exported for tests: this is the function that attaches the credential, so
 *  the header, the resolved baseURL and the timeout are worth pinning. */
export function buildClient(
  baseUrl: string,
  apiBasePath: string,
  readToken: TokenReader,
  timeout: number | undefined,
): AxiosInstance {
  const client = axios.create({
    baseURL: joinBaseUrl(baseUrl, apiBasePath),
    headers: { "Content-Type": "application/json" },
    timeout,
  });

  client.interceptors.request.use(async (config) => {
    const token = await readToken();
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  });

  return client;
}

function TrialMatchesBridgeRoot({
  baseUrl,
  apiBasePath = "",
  ctomopBaseUrl,
  ctomopApiBasePath = "/api",
  getToken,
  sessionKey,
  ...rest
}: TrialMatchesBridgeProps) {
  // TrialMatches' client is derived from props, with no ref in the path. An
  // earlier round read `getToken` through a ref so a host rebuilding the
  // function would not get a new AxiosInstance; that bought nothing and cost
  // correctness. `apiClient` is in no react-query key (`hooks.ts:41`) and
  // TrialMatches' QueryClient is scoped to its mount, so a fresh instance
  // costs one allocation — no refetch, no lost cache. Against that, a ref
  // written during render is not rolled back when React abandons the render,
  // and a ref written from an effect is updated only after the children have
  // committed, so TrialMatches' own subscription could fire through the
  // previous getter. Deriving from props has neither failure mode.
  //
  // The session contract lives in `sessionSignal` below: `sessionKey` when the
  // host passes one, `getToken`'s identity when it does not. Holding a
  // resolved patient across a change of credentials would show one user the
  // previous user's matches on a mount the host reused, and the fallback is
  // the safe default rather than the cheap one — without a `sessionKey`, a
  // host that builds `getToken` inline re-resolves on every render.
  const apiClient = useMemo(
    () =>
      buildClient(
        baseUrl,
        apiBasePath,
        async () => getToken?.(),
        SEARCH_TIMEOUT_MS,
      ),
    [baseUrl, apiBasePath, getToken],
  );

  // The resolution is the one place that needs the latest getter *without*
  // re-running: a token refresh replaces `getToken` while the session is
  // unchanged, and a resolution in flight should carry the fresh token.
  // Written from an effect, never during render, so a render React abandons
  // can never touch it — and declared above the resolution effect, so within a
  // commit the ref is current before the resolution reads it.
  //
  // It cannot cross a session either: a session change re-runs the resolution
  // effect, and cleanup sets `cancelled` during that commit. An async
  // continuation cannot interleave with a commit, so the `cancelled` check
  // before the second call is what guarantees the row and the token belong to
  // the same user.
  const getTokenRef = useRef(getToken);
  useEffect(() => {
    getTokenRef.current = getToken;
  });

  const hasToken = getToken != null;
  // A resolution can fail for reasons that go away: a token that had not
  // arrived, a service that was restarting, a `getToken` the host has since
  // replaced within the same session. None of those re-run the effect on their
  // own, and the only escape was a page reload — which in a host that mounts
  // this on a route is a real cost. One button covers the whole class.
  const [retry, setRetry] = useState(0);
  const shouldLoad = shouldResolvePatient({ ctomopBaseUrl, ...rest });
  const [load, setLoad] = useState<PatientLoad>(
    shouldLoad ? { status: "loading" } : { status: "idle" },
  );

  // Everything that decides *whose* profile a resolved state belongs to. The
  // session half is compared by identity (it may be a function) and so cannot
  // go in the string.
  const routeKey = [
    shouldLoad,
    baseUrl,
    apiBasePath,
    ctomopBaseUrl ?? "",
    ctomopApiBasePath,
  ].join("|");
  const sessionSignal: unknown = resolveSessionSignal(sessionKey, getToken);
  const keyed = hasUsableSessionKey(sessionKey);
  const [session, setSession] = useState({ key: routeKey, signal: sessionSignal });

  // Reset here, in render, and not in the effect below. A passive effect runs
  // after the commit, so the first frame under new credentials would still
  // paint the previous patient's matches — one frame of somebody else's PHI,
  // and long enough for react-query to render its cached list. This is React's
  // documented "adjusting state when a prop changes" pattern: the re-render
  // happens before the browser paints. `current` is used for the rest of this
  // pass so it is correct even in the render that schedules the reset.
  let current = load;
  const reset = nextSessionState(session, { routeKey, sessionSignal, shouldLoad });
  if (reset) {
    current = reset.load;
    setSession(reset.session);
    setLoad(reset.load);
  }

  // `patientInfo: null` reads as "no profile" and suppresses the fetch, which
  // is the documented contract — but `profile ?? null` is how a host with no
  // types spells "I do not have one", and that host then sees the empty-profile
  // screen forever with a `ctomopBaseUrl` it passed and we ignored.
  useEffect(() => {
    if (ctomopBaseUrl && rest.patientInfo === null) {
      warnOnce(
        "explicit-null-patient",
        "`patientInfo: null` was passed alongside `ctomopBaseUrl`, so the " +
          "signed-in patient will not be resolved. Omit `patientInfo` " +
          "entirely to have the bridge fetch it.",
      );
    }
  }, [ctomopBaseUrl, rest.patientInfo]);

  // A signal that changes constantly resets the mount constantly, and each
  // reset re-resolves the patient and takes TrialMatches' filters with it.
  // Two spellings do that: no `sessionKey`, so an inline `getToken` is the
  // signal and every host render is a new one; or a `sessionKey` that is not a
  // scalar, so every host render is a new object. The second is worse and used
  // to be unreportable, because the guard was "no sessionKey was passed".
  //
  // The fourth change is the threshold, not the first: a host with no
  // `sessionKey` is *told* to hand over a new `getToken` when the user
  // changes, so a few resets are the contract working. Only a stream of them
  // says anything.
  const signalChanges = useRef(0);
  const lastSignal = useRef<unknown>(sessionSignal);
  useEffect(() => {
    // Count changes, not effect runs. Refs survive StrictMode's double-invoke
    // of the mount effect, so this short-circuits the second run rather than
    // spending the budget before the host has done anything.
    if (Object.is(lastSignal.current, sessionSignal)) return;
    lastSignal.current = sessionSignal;
    signalChanges.current += 1;
    if (signalChanges.current > 3) {
      warnOnce(
        "session-thrash",
        keyed
          ? "`sessionKey` has changed on nearly every render, so each one " +
              "resets the mount and re-resolves the patient. Pass a scalar " +
              "that changes only with the signed-in user, not an object."
          : "the session signal has changed repeatedly and no usable " +
              "`sessionKey` was passed, so `getToken`'s identity is being " +
              "used and each change resets the mount. Pass a stable " +
              "`getToken` plus a `sessionKey`.",
      );
    }
  }, [sessionSignal, keyed]);

  useEffect(() => {
    // Clear a state that belongs to a previous set of props. Without this the
    // bridge stayed on the spinner (or the error card) forever once the host
    // supplied a patient of its own, because the effect below returns early
    // and nothing else ever wrote the state.
    if (!shouldLoad || !ctomopBaseUrl) {
      setLoad((prev) => (prev.status === "idle" ? prev : { status: "idle" }));
      return;
    }

    let cancelled = false;
    // Already set during render on a session change; this covers a first mount
    // and a retry, without an extra render when it is a no-op.
    setLoad((prev) => (prev.status === "loading" ? prev : { status: "loading" }));

    // Both clients belong to this resolution, and read the ref rather than a
    // captured `getToken` so a refresh mid-flight is picked up. See the note
    // where the ref is written for why that cannot cross a session.
    const readTokenForThisSession: TokenReader = async () => {
      // The interceptor runs after the `cancelled` check below, so check again
      // here rather than resting on an argument about when React commits.
      // Aborting beats sending: a row fetched as one user must never leave
      // under another's credentials, even into a response we would discard.
      if (cancelled) throw new Error("[exact-remote] session changed");
      return getTokenRef.current?.();
    };

    (async () => {
      try {
        // Both clients belong to this resolution — the shared `apiClient` is
        // TrialMatches' and reads the unguarded token.
        const ctomop = buildClient(
          ctomopBaseUrl,
          ctomopApiBasePath,
          readTokenForThisSession,
          RESOLVE_TIMEOUT_MS,
        );
        const exact = buildClient(
          baseUrl,
          apiBasePath,
          readTokenForThisSession,
          RESOLVE_TIMEOUT_MS,
        );
        const me = await ctomop.get("/patient-info/me/");
        const row = (me.data as { patient_info?: PatientInfo })?.patient_info;
        if (!row) {
          if (!cancelled) setLoad({ status: "ready", patientInfo: null });
          return;
        }
        if (cancelled) return;
        // The inline path does no normalisation of its own — receptor statuses
        // to codes, TNM strings to short codes — so run EXACT's normaliser.
        const normalized = await normalizeCtomopRow(exact, row);
        if (!cancelled) setLoad({ status: "ready", patientInfo: normalized });
      } catch (error) {
        // A resolution that moved on is not a failure to report to whoever is
        // looking at the screen now.
        if (cancelled) return;
        const httpStatus = axios.isAxiosError(error)
          ? error.response?.status
          : undefined;
        // An expired token, a wrong `ctomopBaseUrl` and an outage are one
        // message to the user but must not be one message to the operator.
        //
        // Scalars only, never the error object. An AxiosError carries
        // `config.headers.Authorization` — a live bearer token — and, on the
        // normalise call, `config.data`, which is the entire patient row. This
        // remote runs inside somebody else's page, where console-instrumenting
        // SDKs (Sentry breadcrumbs, Datadog RUM, LogRocket) ship console
        // arguments off-box by default.
        console.warn(
          "[exact-remote] could not resolve the signed-in patient",
          {
            httpStatus,
            code: axios.isAxiosError(error) ? error.code : undefined,
            url: axios.isAxiosError(error) ? error.config?.url : undefined,
          },
        );
        setLoad({ status: "error", httpStatus });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [
    shouldLoad,
    baseUrl,
    apiBasePath,
    ctomopBaseUrl,
    ctomopApiBasePath,
    sessionSignal,
    // Whether there is a getter at all, not which one. A host whose session id
    // is ready before its token would otherwise resolve unauthenticated once
    // and land on the error card with nothing to re-run this, because
    // `sessionSignal` never moved.
    hasToken,
    retry,
  ]);

  const patientInfo = selectPatientInfo(rest.patientInfo, current);
  const view = selectBridgeView({
    shouldLoad,
    load: current,
    patientInfo,
    personId: rest.personId,
  });

  if (view === "loading") {
    return (
      <div
        className="exact-root"
        style={{ padding: "1rem" }}
        role="status"
        aria-busy="true"
      >
        Loading your health profile…
      </div>
    );
  }

  if (view === "error") {
    const httpStatus = current.status === "error" ? current.httpStatus : undefined;
    const expired = httpStatus === 401 || httpStatus === 403;
    return (
      <div className="exact-root" style={{ padding: "1rem" }} role="alert">
        <p style={{ margin: "0 0 0.75rem" }}>
          {expired
            ? "Your session has expired. Please sign in again to see trial matches."
            : "Couldn't load your health profile for trial matching."}
        </p>
        {/* Offered on 401/403 too. Signing in is the host's job, but the
            bridge has no way to hear that it happened: a `getToken` that
            exists and returns nothing until auth initialises resolves once,
            401s, and nothing re-runs it — `sessionSignal` never moved and a
            reader was always present. Without this the screen is terminal. */}
        <button
          type="button"
          className="exact-btn-view"
          // The shared button is `width: 100%` for the trial cards it was
          // written for; here it sits under a sentence.
          style={{ width: "auto" }}
          onClick={() => setRetry((n) => n + 1)}
        >
          {expired ? "I've signed in — try again" : "Try again"}
        </button>
      </div>
    );
  }

  // A known-empty profile is not an error and not a developer mistake: it is a
  // patient who has not filled anything in yet. Left to fall through,
  // TrialMatches renders its own "Pass a `patientInfo` payload or `personId`"
  // developer notice — with literal <code> tags — to that user.
  if (view === "no-profile") {
    return (
      <div className="exact-root" style={{ padding: "1rem" }}>
        Add your health profile to see clinical trials matched to you.
      </div>
    );
  }

  // `apiClient` after the spread on purpose: it carries this bridge's auth, and
  // an untyped host copy-pasting the React-host props must not replace it.
  return (
    <TrialMatches
      {...rest}
      apiClient={apiClient}
      patientInfo={patientInfo}
      // `null` is the bridge's spelling of "not given"; TrialMatches' own
      // contract only knows `undefined`.
      personId={rest.personId ?? undefined}
    />
  );
}

const createProvider = createBridgeComponent({
  rootComponent: TrialMatchesBridgeRoot,
});

let provider: ReturnType<typeof createProvider> | null = null;

/** Memoised so `provider().render(…)` and `provider().destroy(…)` share one
 *  `rootMap` — see the note at the top of this file. Roots are keyed by DOM
 *  node inside bridge-react, so one instance still serves many mounts.
 *
 *  One consequence: the factory reads `federationRuntime.instance` in its
 *  body, so the value from the first call is now frozen for the page. Every
 *  use of it is optional-chained, so the only effect is that MF's own
 *  `beforeBridgeRender`/`afterBridgeRender` hooks stay silent if something
 *  calls this before the runtime registers. */
export default function trialMatchesBridgeProvider() {
  provider ??= createProvider();
  return provider;
}
