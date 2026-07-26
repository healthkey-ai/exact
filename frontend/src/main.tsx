// SPA harness entry — `npm run dev` boots this. Renders the federated
// `TrialMatches` component with a minimal local axios instance so you
// can iterate on the remote without standing up PROMOP.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "axios";

import { TrialMatches } from "./federation/TrialMatches";

const queryClient = new QueryClient();
const apiClient = axios.create({
  baseURL: "/api",
  // Token auth: paste your DRF token here for local dev, or wire a
  // real login flow in the dev harness (`src/dev/dev-harness.tsx`).
  headers: import.meta.env.VITE_EXACT_TOKEN
    ? { Authorization: `Token ${import.meta.env.VITE_EXACT_TOKEN}` }
    : {},
});

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root not found");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TrialMatches apiClient={apiClient} queryClient={queryClient} />
    </QueryClientProvider>
  </StrictMode>,
);
