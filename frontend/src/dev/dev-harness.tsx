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
//   `patientInfo` payload (NOT `personId` — see "Why inline" below)
//
// The two backends use mutually-exclusive auth schemes (DRF Token for
// EXACT, Django session cookie for CTOMOP), so the harness keeps two
// separate axios instances — never share, never mix. The TrialMatches
// component only ever sees the token instance.
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
import type { AxiosInstance } from "axios";

import { normalizeCtomopRow } from "../federation/api";
import { TrialMatches } from "../federation/TrialMatches";
import type { PatientInfo } from "../federation/types";
import { CtomopClient } from "./ctomopClient";
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

function Harness() {
  const [token, setToken] = useState<string | null>(() => readStoredToken());
  const [apiClient, setApiClient] = useState<AxiosInstance | null>(() => {
    const t = readStoredToken();
    return t ? makeExactClient(t) : null;
  });
  const [personId, setPersonId] = useState<number | null>(null);
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
            <TrialMatches
              apiClient={apiClient}
              queryClient={queryClient}
              patientInfo={patientInfo}
            />
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
