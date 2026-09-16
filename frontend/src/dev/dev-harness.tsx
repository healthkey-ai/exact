// EXACT federation dev harness — `npm run dev:remote` boots this at
// http://localhost:5177/. The full flow:
//
//   EXACT token login (POST /api-token-auth/)
//     ↓
//   CTOMOP patient picker (session-authed against /ctomop-local or
//   /ctomop-staging via the Vite proxy with Set-Cookie rewriting)
//     ↓
//   Browser fetches the full patient profile from CTOMOP
//   (session cookie is in the browser already — same axios instance
//   as the picker uses)
//     ↓
//   TrialMatches mounted with the EXACT axios instance + inline
//   `patientInfo` payload (NOT `personId` — see "Why inline" below), plus
//   a `state` adapter on its own CTOMOP instance for the per-user writes
//
// The two backends use mutually-exclusive auth schemes (DRF Token for
// EXACT, Django session cookie for CTOMOP), so the harness keeps two
// separate axios instances — never share, never mix. TrialMatches receives
// BOTH, one per purpose: the token instance for trials, and the session
// instance behind `state` for the per-user writes. What it must never
// receive is one instance doing both jobs.
//
// "Per-user writes" is wider than it sounds: `createPromopState` also
// exposes `getWritableFields`/`setPatientFields` against
// `/api/v1/patient-records`, so the inline field editing writes through
// this instance too — and the Local/Staging toggle can point that at the
// deployed CTOMOP. Editing a field here to see the control render edits a
// real record on whichever backend is selected.
//
// Why inline patientInfo (not `personId`):
// EXACT's server-side `?person_id=` resolver (added in #102) fetches
// the patient from CTOMOP using a static `CTOMOP_SERVICE_TOKEN`
// (`CtomopClient.fetch_patient` in EXACT). That path is fine for
// deployments where EXACT has a credentialed identity at CTOMOP, but
// in the dev harness the browser already holds the user's CTOMOP
// session cookie — so it's faster, more correct (matches the picker's
// authz scope), and free of the IDOR concern tracked in #108 to do
// the fetch client-side here and forward the resolved payload inline.
// EXACT's `resolve_patient_info` already prefers the inline `patient_info`
// payload over `?person_id=` when both are present, so this just
// activates the existing fallback path with no backend changes.
import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios, { type AxiosInstance } from "axios";

import { normalizeCtomopRow } from "../federation/api";
import { createPromopState } from "../federation/state";
import { TrialMatches } from "../federation/TrialMatches";
import type { PatientInfo } from "../federation/types";
import { CtomopClient, DEFAULT_TIMEOUT_MS } from "./ctomopClient";
import { CtomopPicker } from "./CtomopPicker";
import { ExactLoginForm } from "./ExactLoginForm";
import { makeExactClient, readStoredToken, writeStoredToken } from "./exactAuth";

const CTOMOP_SOURCE_STORAGE_KEY = "exact-harness-source";

function currentCtomopBase(): string {
  try {
    const v = localStorage.getItem(CTOMOP_SOURCE_STORAGE_KEY);
    if (v === "ctomop-staging") return "/ctomop-staging";
  } catch {
    /* ignore */
  }
  return "/ctomop-local";
}

const queryClient = new QueryClient();

/** The per-user state seam, pointed at the same PROMOP the picker reads.
 *
 *  Without this the harness mounts `TrialMatches` with no `state`, which is
 *  the documented no-adapter path: favorites and the registered tab vanish
 *  and saved filters fall back to `localStorage`. That fallback has no
 *  `preferenceVersioning`, so the conditional-write half of the feature
 *  (#494) could not be exercised locally at all — it was reachable only
 *  through a real host.
 *
 *  Its own axios instance, never the EXACT one: the EXACT client carries an
 *  `Authorization: Token …` and a `/api` baseURL, so sharing it would send
 *  that token to PROMOP and drag PROMOP's cookie onto EXACT. This file's own
 *  header says the same thing at the top.
 *
 *  `withCredentials` because PROMOP authenticates the harness by session
 *  cookie, which rides whichever `/ctomop-*` proxy the source toggle
 *  selects — same-origin either way. */
function promopStateFor(personId: number) {
  return createPromopState({
    client: axios.create({
      baseURL: currentCtomopBase(),
      withCredentials: true,
      // The same bound `CtomopClient` uses, from the same constant rather
      // than a third copy of the number. Without it a paused PROMOP leaves
      // the star spinning and "Saving…" unresolved forever while the picker
      // times out and says so — two stories from one stalled backend.
      timeout: DEFAULT_TIMEOUT_MS,
      // No `xsrfCookieName`/`xsrfHeaderName`, deliberately. PROMOP's DRF
      // stack authenticates with `CsrfExemptSessionAuthentication`, whose
      // `enforce_csrf` is a no-op, so a session PATCH carries no CSRF token
      // and is accepted — verified against a running promop, not inferred.
      // Written down because three separate reviews read the session cookie
      // and concluded these writes must 403.
    }),
    personId,
  });
}

function Harness() {
  const [token, setToken] = useState<string | null>(() => readStoredToken());
  const [apiClient, setApiClient] = useState<AxiosInstance | null>(() => {
    const t = readStoredToken();
    return t ? makeExactClient(t) : null;
  });
  const [personId, setPersonId] = useState<number | null>(null);
  // Per-user state is cached by PATIENT, not by backend: `TrialMatches`
  // derives its query keys from the patient, and the picker's Local/Staging
  // toggle changes which promop those keys point at without changing the
  // keys. Before this file passed a `state` adapter there was no per-user
  // state to cache and the question did not arise; now the same person_id on
  // the other source would read — and write against — the previous source's
  // favorites, registrations and saved filters.
  //
  // Dropping the cache on a source change is the blunt fix and the right one
  // here — nothing is lost, because the toggle also clears the selection.
  //
  // Note the ordering: `ctomopBase` is read during render, and if no patient
  // is selected the picker's `onSelect(null)` sets `personId` to the `null`
  // it already holds, so React bails out and this effect does not run until
  // the NEXT render — the one that selects a patient. The wipe still lands
  // before that render mounts `TrialMatches`, because the mount is gated on
  // `patientInfo` by the ternary below and nothing was mounted to begin
  // with. So the cost of the late wipe is a refetch, not stale cross-source
  // data — and the guard that makes that true is the render-level gate, not
  // the effect that nulls `patientInfo`.
  const ctomopBase = currentCtomopBase();
  const lastCtomopBase = useRef(ctomopBase);
  useEffect(() => {
    if (lastCtomopBase.current === ctomopBase) return;
    lastCtomopBase.current = ctomopBase;
    queryClient.clear();
  }, [ctomopBase]);
  const [patientInfo, setPatientInfo] = useState<PatientInfo | null>(null);
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  const handleTokenObtained = useCallback((next: string) => {
    setToken(next);
    writeStoredToken(next);
    setApiClient(makeExactClient(next));
  }, []);

  const handleSignOut = useCallback(() => {
    setToken(null);
    writeStoredToken(null);
    setApiClient(null);
    setPersonId(null);
    setPatientInfo(null);
    setResolveError(null);
    // Best-effort CTOMOP session cleanup so the user isn't left logged
    // into the wrong account on the staging host after switching dev
    // identities. `logout()` tolerates 401/403 internally.
    void new CtomopClient(currentCtomopBase()).logout();
    // And drop the cache, for the same reason the source toggle does. The
    // per-user state this file now wires up is keyed by PATIENT, not by the
    // signed-in identity: sign out, sign in as someone else, pick the same
    // person, and react-query would answer from the previous identity's
    // favorites and saved filters until they went stale. Sign-out is exactly
    // where that has to stop.
    queryClient.clear();
  }, []);

  // When a patient is picked, fetch the full patient profile from
  // CTOMOP using the user's session cookie, then pipe it through
  // EXACT's `POST /normalize-ctomop-row/` so the matcher sees
  // EXACT-shaped values (receptor statuses → codes, TNM stripping,
  // therapy-outcome label → ID, etc.) — mirroring what the server-side
  // `?person_id=` resolver does. Without this chain step a meaningful
  // subset of fields silently reads as "unknown" for CTOMOP-resolved
  // patients.
  //
  // The previous resolved payload is cleared first so a stale profile
  // can't leak into the new patient's TrialMatches mount, and the
  // cancellation token aborts state updates if the user picks another
  // patient mid-flight.
  useEffect(() => {
    if (personId == null || apiClient == null) {
      setPatientInfo(null);
      setResolveError(null);
      setResolving(false);
      return;
    }
    let cancelled = false;
    setResolving(true);
    setResolveError(null);
    setPatientInfo(null);
    (async () => {
      try {
        const detail = await new CtomopClient(currentCtomopBase()).getPatient(personId);
        if (cancelled) return;
        const raw = (detail.patient_info ?? null) as PatientInfo | null;
        if (!raw) {
          setPatientInfo(null);
          return;
        }
        const normalized = await normalizeCtomopRow(apiClient, raw);
        if (cancelled) return;
        setPatientInfo(normalized);
      } catch (e) {
        if (cancelled) return;
        // Coerce non-Error throws so the UI never renders "undefined".
        // axios sometimes rejects with `{message, response, …}` objects
        // that aren't `Error` instances depending on the adapter, and a
        // direct `(e as Error).message` would silently render an empty
        // string in those cases.
        const msg =
          e instanceof Error
            ? e.message
            : typeof e === "string"
              ? e
              : "Unknown error";
        setResolveError(msg);
      } finally {
        if (!cancelled) setResolving(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [personId, apiClient]);

  // Token from `VITE_EXACT_TOKEN` env wins on first mount only — a
  // `.env.local` skips the form for a faster dev loop. We deliberately
  // do NOT re-apply the env token on every render (the obvious
  // `[token, handleTokenObtained]` deps would hijack Sign-out: clearing
  // the token triggers the effect, which re-installs the env token,
  // making Sign-out a no-op while `VITE_EXACT_TOKEN` is set).
  const envApplied = useRef(false);
  useEffect(() => {
    if (envApplied.current) return;
    envApplied.current = true;
    const envToken = import.meta.env.VITE_EXACT_TOKEN;
    if (envToken && !token) {
      handleTokenObtained(envToken);
    }
  }, [token, handleTokenObtained]);

  if (!apiClient) {
    return (
      <div style={{ padding: "1.5rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
        <h1 style={{ margin: 0, fontSize: "1.25rem" }}>EXACT Federation Dev Harness</h1>
        <p style={{ color: "#6b7280", margin: 0, maxWidth: "32rem" }}>
          Sign in to EXACT to load the harness. The token is stored locally;
          sign out to clear it. CTOMOP login is requested separately when
          the patient list endpoint returns 401.
        </p>
        <ExactLoginForm onTokenObtained={handleTokenObtained} />
      </div>
    );
  }

  return (
    <div style={{ padding: "1rem" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: "1rem",
          marginBottom: "1rem",
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: "1.25rem" }}>EXACT Federation Dev Harness</h1>
          <p style={{ color: "#6b7280", marginTop: "0.25rem", marginBottom: 0 }}>
            Pick a CTOMOP patient → harness fetches the profile (browser-side
            session cookie) → TrialMatches mounts with inline
            <code style={{ marginLeft: "0.25rem" }}>patientInfo</code>.
          </p>
        </div>
        <button
          type="button"
          onClick={handleSignOut}
          style={{
            padding: "0.25rem 0.625rem",
            background: "transparent",
            border: "1px solid #d1d5db",
            borderRadius: "0.25rem",
            cursor: "pointer",
            font: "inherit",
            color: "#6b7280",
          }}
        >
          Sign out
        </button>
      </header>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(20rem, 1fr) minmax(0, 2fr)",
          gap: "1.5rem",
        }}
      >
        <CtomopPicker onSelect={setPersonId} selectedPersonId={personId} />
        <div>
          {personId == null ? (
            <p style={{ color: "#6b7280" }}>
              Pick a CTOMOP patient to load their trial matches.
            </p>
          ) : resolving ? (
            <p style={{ color: "#6b7280" }}>
              Fetching patient profile from CTOMOP…
            </p>
          ) : resolveError ? (
            <div
              style={{
                padding: "0.75rem",
                border: "1px solid #fca5a5",
                background: "#fef2f2",
                color: "#991b1b",
                borderRadius: "0.25rem",
                fontSize: "0.875rem",
              }}
            >
              Failed to fetch CTOMOP patient profile: {resolveError}
            </div>
          ) : patientInfo != null ? (
            <>
              {/* On the screen, not only in a comment. Passing `state` turned
                  the harness into a WRITE client: the star is a PATCH to
                  trial-enrollments and a field pencil is a PATCH to
                  patient-records. Against `/ctomop-staging` those land on a
                  deployed, shared record — and once a patient is loaded the
                  two backends look identical. The person who trips this is
                  reading the page, not this file. */}
              {currentCtomopBase() === "/ctomop-staging" ? (
                <div
                  style={{
                    marginBottom: "0.75rem",
                    padding: "0.5rem 0.75rem",
                    border: "1px solid #fbbf24",
                    background: "#fffbeb",
                    color: "#92400e",
                    borderRadius: "0.25rem",
                    fontSize: "0.875rem",
                  }}
                >
                  <strong>Staging CTOMOP.</strong> Bookmarks, registrations,
                  saved filters and inline field edits write to the deployed
                  backend, against this patient's real record.
                </div>
              ) : null}
              <TrialMatches
                apiClient={apiClient}
                queryClient={queryClient}
                patientInfo={patientInfo}
                state={promopStateFor(personId)}
              />
            </>
          ) : (
            <p style={{ color: "#6b7280" }}>
              CTOMOP returned an empty patient profile.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root not found");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>
  </StrictMode>,
);
