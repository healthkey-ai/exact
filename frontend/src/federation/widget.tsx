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

import { injectStyles } from "./injectStyles";
import { TrialMatches } from "./TrialMatches";
import type { TrialPreferenceStore } from "./state";
import type { PatientInfo } from "./types";

export interface MountOptions {
  /** EXACT API base the widget's axios points at. Default "/". In CB this is the
   *  path CB proxies to its in-process EXACT endpoints (e.g. "/exact-api"). */
  apiBase?: string;
  /** EXACT DRF token; sent as `Authorization: Token <token>` when present. */
  token?: string;
  /** PROMOP person_id — EXACT resolves the patient server-side (#102).
   *
   *  Deployment-gated: the resolve path is an IDOR risk (the CTOMOP fetch
   *  uses a service token not bound to the caller), so it answers 403
   *  — "person_id lookup is disabled. Provide an inline patient_info payload
   *  instead." — unless the EXACT instance sets
   *  `EXACT_ALLOW_PERSON_ID_LOOKUP`, which is off by default outside
   *  local/DEBUG. A host on a deployment that leaves it off passes
   *  `patientInfo` instead. */
  personId?: string | number;
  /** Inline patient payload — the alternative to `personId` (existing CB contract). */
  patientInfo?: PatientInfo;
  /** Where the patient's saved search settings are kept.
   *
   *  Plain async functions, which is all that crosses this boundary — no
   *  React, no class instances. A host that keeps these on its own user row
   *  (CB keeps the suitability weights there) implements them against its
   *  own API; without them the settings go to this browser's `localStorage`,
   *  which survives a reload. A store that persists only PART of the set —
   *  CB round-trips the four suitability weights and nothing else — replaces
   *  that fallback rather than joining it, so the rest stops being
   *  remembered.
   *
   *  Deliberately NOT the whole `TrialStateAdapter`: bookmarks and
   *  registrations live somewhere this host does not talk to, and a stub
   *  adapter would light up tabs and a bookmark control with nowhere to
   *  write. */
  preferences?: TrialPreferenceStore;
}

// One React root per host element, so a re-mount on the same node replaces cleanly.
const roots = new WeakMap<HTMLElement, Root>();

/** Mount TrialMatches into `el`. Returns a disposer; also see `unmount`. */
export function mount(el: HTMLElement, opts: MountOptions = {}): () => void {
  unmount(el); // idempotent: tear down any prior mount on this node first

  // Before anything renders, so this is the call that sets the mode for this
  // bundle — TrialMatches injects on mount too, and inherits it rather than
  // adding a second copy of the sheet. Unlayered on purpose: a host that needs this build is a
  // host that cannot share our React tree, and it will not be declaring our
  // cascade layer either — CB's `ui/` ships Tailwind v3's unlayered preflight,
  // which beats every layered rule of ours whatever its specificity. Measured
  // there: no background on the CTA, a 14px title, no padding on the
  // segmented controls. See `injectStyles`.
  injectStyles({ layered: false });

  const apiClient = axios.create({
    baseURL: opts.apiBase ?? "/",
    headers: opts.token ? { Authorization: `Token ${opts.token}` } : undefined,
  });
  // Handed to TrialMatches as well as to the provider: given none, it builds
  // its own, and this one would be an allocation nothing reads. Its defaults
  // are react-query's — three retries with backoff on a whole-corpus matcher
  // call — and bounding that belongs with whoever owns the retry policy, the
  // same call `TrialMatchesBridge` makes and documents.
  const queryClient = new QueryClient();

  const root = createRoot(el);
  roots.set(el, root);
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={apiClient}
          queryClient={queryClient}
          personId={opts.personId}
          patientInfo={opts.patientInfo}
          preferences={opts.preferences}
        />
      </QueryClientProvider>
    </StrictMode>,
  );

  // Disposes THIS mount, not whatever is on the element by then. A host that
  // re-mounts (CB's page re-runs its effect when the token or the person
  // changes) and only then runs the previous cleanup would otherwise tear
  // down the live mount with the stale disposer, and the page goes blank.
  return () => {
    if (roots.get(el) === root) unmount(el);
  };
}

/** Unmount a previously mounted widget from `el` (safe if nothing is mounted). */
export function unmount(el: HTMLElement): void {
  const root = roots.get(el);
  if (root) {
    root.unmount();
    roots.delete(el);
  }
}
