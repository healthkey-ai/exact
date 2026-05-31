// EXACT federation dev harness — `npm run dev:remote` boots this at
// http://localhost:5177/. The full flow:
//
//   EXACT token login (POST /api-token-auth/)
//     ↓
//   CTOMOP patient picker (session-authed against /ctomop-local or
//   /ctomop-staging via the Vite proxy with Set-Cookie rewriting)
//     ↓
//   TrialMatches mounted with the EXACT axios instance + person_id
//
// The two backends use mutually-exclusive auth schemes (DRF Token for
// EXACT, Django session cookie for CTOMOP), so the harness keeps two
// separate axios instances — never share, never mix. The TrialMatches
// component only ever sees the token instance.
import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AxiosInstance } from "axios";

import { TrialMatches } from "../federation/TrialMatches";
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
    // Best-effort CTOMOP session cleanup so the user isn't left logged
    // into the wrong account on the staging host after switching dev
    // identities. `logout()` tolerates 401/403 internally.
    void new CtomopClient(currentCtomopBase()).logout();
  }, []);

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
            Pick a CTOMOP patient → mount TrialMatches with the resolved
            <code style={{ marginLeft: "0.25rem" }}>person_id</code>.
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
          ) : (
            <TrialMatches
              apiClient={apiClient}
              queryClient={queryClient}
              personId={personId}
            />
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
