/**
 * Framework-agnostic mount for TrialMatches.
 *
 * `./TrialMatches` (the plain React component) is unchanged, for React hosts
 * such as ht-phr. This entry serves hosts that are not React — HealthTree ONE
 * is SvelteKit — and exposes the provider contract:
 *
 *     const provider = await loadRemote("exact_remote/TrialMatchesBridge");
 *     await provider().render({ dom, baseUrl, getToken, ... });
 *     provider().destroy({ moduleName, dom });
 *
 * Props are data-only: the host passes `baseUrl` + `getToken` instead of a live
 * AxiosInstance, so it needs neither axios nor react-query, and EXACT keeps
 * ownership of how it calls its own API.
 * See ht-phr/docs/mf-bridge-proposal.md §6.2.
 */
import { useEffect, useMemo, useState } from "react";
import axios, { type AxiosInstance } from "axios";
import { createBridgeComponent } from "@module-federation/bridge-react/v19";

import TrialMatches from "./TrialMatches";
import type { TrialMatchesProps } from "./types";

export interface TrialMatchesBridgeProps
  extends Omit<TrialMatchesProps, "apiClient" | "queryClient"> {
  /** Service origin, e.g. "https://exact-staging-….run.app". */
  baseUrl: string;
  /**
   * PRomop origin. When given (and no `patientInfo`/`personId` is passed), the
   * bridge loads the signed-in patient itself: PRomop's /patient-info/me/ with
   * the caller's own token, then EXACT's /normalize-ctomop-row/.
   *
   * That two-step is EXACT's business, not the host's, so it lives here rather
   * than being reimplemented by every host. It also has to be the caller's
   * token: EXACT's server-side `person_id` resolver is disabled by default
   * because its CTOMOP service token is not bound to the caller and would let
   * any person_id through (IDOR). Fetching "me" makes PRomop enforce access.
   */
  ctomopBaseUrl?: string;
  /** Appended to baseUrl to form the axios baseURL. */
  apiBasePath?: string;
  /** Resolves the caller's bearer token; the host owns authentication. */
  getToken?: () => Promise<string | null | undefined> | string | null | undefined;
}

function buildClient(
  baseUrl: string,
  apiBasePath: string,
  getToken?: TrialMatchesBridgeProps["getToken"],
): AxiosInstance {
  const client = axios.create({
    baseURL: `${baseUrl.replace(/\/$/, "")}${apiBasePath}`,
    headers: { "Content-Type": "application/json" },
  });

  client.interceptors.request.use(async (config) => {
    if (!getToken) return config;
    const token = await getToken();
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  });

  return client;
}

type PatientLoad =
  | { status: "idle" | "loading" }
  | { status: "ready"; patientInfo: TrialMatchesProps["patientInfo"] }
  | { status: "error" };

function TrialMatchesBridgeRoot({
  baseUrl,
  apiBasePath = "",
  ctomopBaseUrl,
  getToken,
  ...rest
}: TrialMatchesBridgeProps) {
  const apiClient = useMemo(
    () => buildClient(baseUrl, apiBasePath, getToken),
    [baseUrl, apiBasePath, getToken],
  );

  // Only when the host has not resolved the patient itself.
  const shouldLoad = Boolean(ctomopBaseUrl) && !rest.patientInfo && !rest.personId;
  const [load, setLoad] = useState<PatientLoad>({ status: shouldLoad ? "loading" : "idle" });

  useEffect(() => {
    if (!shouldLoad || !ctomopBaseUrl) return;
    let cancelled = false;

    (async () => {
      try {
        const ctomop = buildClient(ctomopBaseUrl, "/api", getToken);
        const me = await ctomop.get("/patient-info/me/");
        const row = (me.data as { patient_info?: Record<string, unknown> })?.patient_info;
        if (!row) {
          if (!cancelled) setLoad({ status: "ready", patientInfo: null });
          return;
        }
        // The inline path does no normalisation of its own — receptor statuses
        // to codes, TNM strings to short codes — so run EXACT's normaliser.
        const normalized = await apiClient.post("/normalize-ctomop-row/", row);
        if (!cancelled) {
          setLoad({ status: "ready", patientInfo: normalized.data as TrialMatchesProps["patientInfo"] });
        }
      } catch {
        if (!cancelled) setLoad({ status: "error" });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [shouldLoad, ctomopBaseUrl, getToken, apiClient]);

  if (load.status === "loading") {
    return <div className="exact-root" style={{ padding: "1rem" }}>Loading your health profile…</div>;
  }

  if (load.status === "error") {
    return (
      <div className="exact-root" style={{ padding: "1rem" }} role="alert">
        Couldn't load your health profile for trial matching. Please refresh to try again.
      </div>
    );
  }

  const patientInfo = load.status === "ready" ? load.patientInfo : rest.patientInfo;

  return <TrialMatches apiClient={apiClient} {...rest} patientInfo={patientInfo} />;
}

export default createBridgeComponent({ rootComponent: TrialMatchesBridgeRoot });
