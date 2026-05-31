// Federation dev harness — `npm run dev:remote` boots this.
// Scaffold version: renders the federated `TrialMatches` directly with a
// local axios instance. The CTOMOP picker + token-login flow lands in
// #104 (Track C of #101).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "axios";

import { TrialMatches } from "../federation/TrialMatches";

const queryClient = new QueryClient();
const apiClient = axios.create({
  baseURL: "/api",
  headers: import.meta.env.VITE_EXACT_TOKEN
    ? { Authorization: `Token ${import.meta.env.VITE_EXACT_TOKEN}` }
    : {},
});

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root not found");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <div style={{ padding: "1rem", fontFamily: "sans-serif" }}>
        <h1 style={{ margin: 0, fontSize: "1.25rem" }}>
          EXACT Federation Dev Harness
        </h1>
        <p style={{ color: "#6b7280", marginTop: "0.25rem" }}>
          Scaffold. CTOMOP picker + token login arrive in #104.
        </p>
        <hr style={{ margin: "1rem 0", border: "none", borderTop: "1px solid #e5e7eb" }} />
        <TrialMatches apiClient={apiClient} queryClient={queryClient} />
      </div>
    </QueryClientProvider>
  </StrictMode>,
);
