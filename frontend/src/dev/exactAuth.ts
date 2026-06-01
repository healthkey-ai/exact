// EXACT token-login helper for the dev harness.
//
// EXACT uses DRF Token auth: `POST /api-token-auth/` with
// `{username, password}` returns `{token}`. The harness stashes the
// token in localStorage so a refresh keeps the session, and injects it
// as `Authorization: Token <…>` on a separate axios instance from the
// CTOMOP one (cookie + token must NOT mix on the same client — the
// browser will happily send both, but CTOMOP's `withCredentials` would
// also drag along EXACT's cross-site CSRF cookie if we shared
// instances).
import axios, { AxiosError, type AxiosInstance } from "axios";

const TOKEN_STORAGE_KEY = "exact-harness-token";
const DEFAULT_BASE = "/api";
const DEFAULT_TIMEOUT_MS = 10_000;

interface ExactErrorBody {
  detail?: string;
  non_field_errors?: string[];
}

export function readStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeStoredToken(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    /* ignore quota / disabled */
  }
}

export async function obtainExactToken(
  username: string,
  password: string,
  baseURL: string = DEFAULT_BASE,
): Promise<string> {
  try {
    const res = await axios.post<{ token: string }>(
      `${baseURL}/api-token-auth/`,
      { username, password },
      { timeout: DEFAULT_TIMEOUT_MS },
    );
    return res.data.token;
  } catch (e) {
    const ax = e as AxiosError<ExactErrorBody>;
    const status = ax.response?.status;
    const fallback =
      status === 400 || status === 401
        ? "Login rejected — check username and password."
        : (ax.message ?? "Login failed.");
    const detail =
      ax.response?.data?.detail ??
      ax.response?.data?.non_field_errors?.[0] ??
      fallback;
    throw new Error(detail);
  }
}

/** Build the axios instance the host hands to `TrialMatches`. The token
 *  is required — without it EXACT's DRF returns 401 for every authed
 *  endpoint. */
export function makeExactClient(
  token: string,
  baseURL: string = DEFAULT_BASE,
): AxiosInstance {
  return axios.create({
    baseURL,
    timeout: DEFAULT_TIMEOUT_MS,
    headers: { Authorization: `Token ${token}` },
    // EXACT's `/trials/` reads patient context from the request body
    // even on GET (matches the existing CB inline contract). axios v1's
    // default XHR adapter silently drops the body on GET — the request
    // reaches Django with no `patient_info`, the matcher runs with no
    // patient context, and the response is the disease-agnostic union.
    // The fetch adapter honours GET-with-body, so EXACT actually
    // receives the payload. (Verified end-to-end against EXACT's
    // runserver: the XHR path logged 0-byte bodies, the fetch path
    // logs full payloads.)
    adapter: "fetch",
  });
}
