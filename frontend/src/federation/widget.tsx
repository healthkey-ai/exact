// Self-contained "widget" build of TrialMatches for hosts that CANNOT share a
// single React tree with this remote — specifically CB's `ui/`, which is React
// 18 while this remote is React 19. The Module-Federation `remoteEntry.js` path
// (vite.remote.config.ts) shares React as a singleton, so a React-18 host would
// hand its own React to this React-19 code and break. This build instead bundles
// its OWN React 19 + QueryClient and exposes an imperative, framework-agnostic
// `mount(el, opts)` / `unmount(el)` the host calls with plain values (a token +
// an apiBase + a personId), never a React reference. Isolated by construction.
//
// Consume from a host:
//   const { mount } = await import("<exact>/widget/exact-trials.js");
//   const dispose = mount(el, { apiBase: "/exact-api", token, personId });
//   // later: dispose();  (or unmount(el))
import { StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "axios";

import { TrialMatches } from "./TrialMatches";
import type { PatientInfo } from "./types";

export interface MountOptions {
  /** EXACT API base the widget's axios points at. Default "/". In CB this is the
   *  path CB proxies to its in-process EXACT endpoints (e.g. "/exact-api"). */
  apiBase?: string;
  /** EXACT DRF token; sent as `Authorization: Token <token>` when present. */
  token?: string;
  /** PROMOP person_id — EXACT resolves the patient server-side (#102). */
  personId?: number;
  /** Inline patient payload — the alternative to `personId` (existing CB contract). */
  patientInfo?: PatientInfo;
}

// One React root per host element, so a re-mount on the same node replaces cleanly.
const roots = new WeakMap<HTMLElement, Root>();

/** Mount TrialMatches into `el`. Returns a disposer; also see `unmount`. */
export function mount(el: HTMLElement, opts: MountOptions = {}): () => void {
  unmount(el); // idempotent: tear down any prior mount on this node first

  const apiClient = axios.create({
    baseURL: opts.apiBase ?? "/",
    headers: opts.token ? { Authorization: `Token ${opts.token}` } : undefined,
  });
  const queryClient = new QueryClient();

  const root = createRoot(el);
  roots.set(el, root);
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={apiClient}
          personId={opts.personId}
          patientInfo={opts.patientInfo}
        />
      </QueryClientProvider>
    </StrictMode>,
  );

  return () => unmount(el);
}

/** Unmount a previously mounted widget from `el` (safe if nothing is mounted). */
export function unmount(el: HTMLElement): void {
  const root = roots.get(el);
  if (root) {
    root.unmount();
    roots.delete(el);
  }
}
