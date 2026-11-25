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
    // 404 usually means the endpoint is not mounted — a backend flag rather
    // than anything the reader typed. Saying "Request failed with status
    // code 404" sends them looking at their password instead.
    const fallback =
      status === 404
        ? "No /api-token-auth/ on this backend. Usually ENABLE_DRF_TOKEN_AUTH is off — set it to true, or run with DEBUG or an explicitly set ENVIRONMENT=local, which is only what it DEFAULTS to. A wrong proxy target does this too."
        : status === 400 || status === 401
          ? // Kept, not replaced by the hint below. A 400 does not always
            // carry `detail` or `non_field_errors` — a blank username is a
            // per-FIELD error — and without this the reader would get
            // "Request failed with status code 400".
            "Login rejected — check the subject and password."
          : (ax.message ?? "Login failed.");
    // Per-FIELD errors too, not just `detail`/`non_field_errors`. DRF answers
    // a blank username with `{"username": ["This field may not be blank."]}`
    // — reachable, because `required` blocks "" but not "   " — and reading
    // only the two general keys threw that away for a generic line.
    //
    // Gated on 400, and narrowly. Ungated it outranks the 404 message this
    // whole change exists to deliver: a proxy that answers JSON rather than
    // Django's HTML would render `error: upstream unavailable` and the
    // ENABLE_DRF_TOKEN_AUTH guidance would never appear — and that is the
    // case the 404 text itself calls out. Arrays are excluded for the same
    // reason: `typeof [] === "object"`, so `["gateway down"]` would read as
    // field `0`.
    const body = ax.response?.data;
    const firstFieldError = (): string | undefined => {
      if (status !== 400) return undefined;
      if (!body || typeof body !== "object" || Array.isArray(body)) return undefined;
      // The form calls it Subject; `username` is only what the wire calls
      // it. Naming a field that appears nowhere on screen is the defect
      // this change was filed against.
      const label = (field: string) => (field === "username" ? "Subject" : field);
      const messageFor = (value: unknown): string | undefined => {
        const first = Array.isArray(value) ? value[0] : value;
        return typeof first === "string" ? first : undefined;
      };
      // The field the reader can act on first, rather than whichever key the
      // server happened to serialize first — the appended hint talks about
      // the subject, so a message naming `password` would point elsewhere.
      const entries = Object.entries(body as Record<string, unknown>).filter(
        ([field]) => field !== "detail" && field !== "non_field_errors",
      );
      const preferred =
        entries.find(([field]) => field === "username") ?? entries[0];
      if (!preferred) return undefined;
      const message = messageFor(preferred[1]);
      return message === undefined ? undefined : `${label(preferred[0])}: ${message}`;
    };
    const detail =
      // On 404 the body is ignored outright. A DRF-shaped `{"detail": "Not
      // found."}` is the ordinary answer from a misrouted proxy, and reading
      // it ahead of the fallback restates the status code while suppressing
      // the only sentence that says what to do about it. Nothing a 404 body
      // can carry beats naming the flag.
      status === 404
        ? fallback
        : (ax.response?.data?.detail ??
          ax.response?.data?.non_field_errors?.[0] ??
          firstFieldError() ??
          fallback);
    // APPENDED, not used as a fallback. `ObtainAuthToken` answers 400 with a
    // populated `non_field_errors`, so a fallback string never reaches the
    // reader on the one status it was written for: they would keep seeing
    // "Unable to log in with provided credentials" and keep retyping a
    // password that was never the problem.
    const hint =
      status === 400 || status === 401
        ? "EXACT's username is the identity's sub, not the email — check that first."
        : "";
    // Joined with a separator rather than concatenated: `detail` comes from
    // the server and does not reliably end in punctuation.
    throw new Error([detail, hint].filter(Boolean).join(" · "));
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
  });
}
