// Thin CTOMOP HTTP client for the EXACT dev harness (PR 3 of #101).
//
// CTOMOP exposes a session-authenticated DRF API at `/api/patient-info/`.
// We keep the cookie jar in the browser (axios `withCredentials: true`)
// and surface only the calls the harness needs: ping, login, list, fetch
// one. This file is consumed exclusively by `dev-harness.tsx` and never
// reaches the published `remoteEntry.js` bundle (federation build excludes
// `src/dev/` via `rollupOptions.input: {}` in `vite.remote.config.ts`).
//
// Adapted from SoC's `frontend/src/dev/ctomopClient.ts` — same wire
// contract, same proxy strategy.
import axios, { AxiosError, type AxiosInstance } from "axios";

// Fallback base for callers that don't pass one. CTOMOP sets
// `SESSION_COOKIE_SECURE = True` + `CSRF_COOKIE_SECURE = True`, so going
// same-origin via a dev proxy mount is the only path where the session
// cookie survives a plain-http dev loop.
const DEFAULT_BASE = "/ctomop-local";
const DEFAULT_TIMEOUT_MS = 10_000;
const PING_TIMEOUT_MS = 2_000;

function envBase(): string {
  return import.meta.env.VITE_CTOMOP_BASE || DEFAULT_BASE;
}

export interface CtomopPatientSummary {
  id: number;
  /** Always an integer per CTOMOP's `PatientListSerializer`. */
  person_id: number;
  patient_name?: string | null;
  age?: number | null;
  disease?: string | null;
  stage?: string | null;
  updated_at?: string | null;
}

export interface CtomopPatientDetail {
  patient_info: Record<string, unknown>;
  user?: Record<string, unknown> | null;
}

/** CTOMOP error responses are `{error: "<message>"}`; DRF defaults emit
 *  `{detail: "..."}` for 401/403 from permission classes — read both. */
interface CtomopErrorBody {
  error?: string;
  detail?: string;
}

function ctomopErrorMessage(
  ax: AxiosError<CtomopErrorBody>,
  fallback: string,
): string {
  return ax.response?.data?.error ?? ax.response?.data?.detail ?? fallback;
}

export class CtomopClient {
  readonly base: string;
  private client: AxiosInstance;

  constructor(base?: string) {
    this.base = base ?? envBase();
    this.client = axios.create({
      baseURL: this.base,
      withCredentials: true,
      timeout: DEFAULT_TIMEOUT_MS,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
  }

  async login(username: string, password: string): Promise<void> {
    try {
      await this.client.post("/api/auth/login/", { username, password });
    } catch (e) {
      const ax = e as AxiosError<CtomopErrorBody>;
      const status = ax.response?.status;
      const fallback =
        status === 400 || status === 401
          ? "Login rejected — check username and password."
          : (ax.message ?? "Login failed.");
      throw new Error(ctomopErrorMessage(ax, fallback));
    }
  }

  async logout(): Promise<void> {
    // Tolerate 401/403 — if we never had a session, "logged out" is the
    // desired end state regardless.
    try {
      await this.client.post("/api/auth/logout/");
    } catch {
      /* ignore */
    }
  }

  async listPatients(): Promise<CtomopPatientSummary[]> {
    const res = await this.client.get<
      CtomopPatientSummary[] | { results: CtomopPatientSummary[] }
    >("/api/patient-info/");
    return Array.isArray(res.data) ? res.data : (res.data.results ?? []);
  }

  async getPatient(personId: number | string): Promise<CtomopPatientDetail> {
    const res = await this.client.get<CtomopPatientDetail>(
      `/api/patient-info/${encodeURIComponent(String(personId))}/`,
    );
    return res.data;
  }

  /** Reachability check. Anything CTOMOP responds to — including 5xx,
   *  405, 404 — counts as reachable; only a network/timeout failure
   *  (`ax.response` undefined) means the host is genuinely down. */
  async ping(): Promise<boolean> {
    try {
      await this.client.get("/api/patient-info/", {
        timeout: PING_TIMEOUT_MS,
      });
      return true;
    } catch (e) {
      const ax = e as AxiosError;
      return Boolean(ax.response);
    }
  }
}
