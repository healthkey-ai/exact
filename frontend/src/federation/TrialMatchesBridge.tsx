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
import { useMemo } from "react";
import axios, { type AxiosInstance } from "axios";
import { createBridgeComponent } from "@module-federation/bridge-react/v19";

import TrialMatches from "./TrialMatches";
import type { TrialMatchesProps } from "./types";

export interface TrialMatchesBridgeProps
  extends Omit<TrialMatchesProps, "apiClient" | "queryClient"> {
  /** Service origin, e.g. "https://exact-staging-….run.app". */
  baseUrl: string;
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

function TrialMatchesBridgeRoot({
  baseUrl,
  apiBasePath = "",
  getToken,
  ...rest
}: TrialMatchesBridgeProps) {
  const apiClient = useMemo(
    () => buildClient(baseUrl, apiBasePath, getToken),
    [baseUrl, apiBasePath, getToken],
  );

  return <TrialMatches apiClient={apiClient} {...rest} />;
}

export default createBridgeComponent({ rootComponent: TrialMatchesBridgeRoot });
