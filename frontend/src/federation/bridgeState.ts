/* Pure helpers behind `TrialMatchesBridge`.
 *
 * They live outside the component so the two decisions that decide *which
 * patient's data reaches the matcher* can be unit-tested without a DOM: the
 * bridge itself has no test harness in this repo (no jsdom, no
 * @testing-library), and "which patient" is not something to leave to a
 * manual check.
 */

import type { PatientInfo } from "./types";

/** Outcome of the bridge's self-driven `/patient-info/me/` resolution. */
export type PatientLoad =
  /** Nothing to resolve — the host supplied the patient, or no `ctomopBaseUrl`. */
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; patientInfo: PatientInfo | null }
  | { status: "error"; httpStatus?: number };

/** Join a service origin with an API base path.
 *
 *  Both halves are host-supplied, so neither slash can be assumed: `"api"`
 *  used to concatenate straight onto the origin and produce `https://hostapi`.
 */
export function joinBaseUrl(origin: string, basePath: string): string {
  const trimmed = origin.trim().replace(/\/+$/, "");
  const path = basePath.trim().replace(/^\/+/, "").replace(/\/+$/, "");
  return path ? `${trimmed}/${path}` : trimmed;
}

/** Decide which patient payload reaches `TrialMatches`.
 *
 *  An explicit prop always wins, on every render — including after the bridge
 *  has already resolved somebody. The previous form (`ready ? fetched : prop`)
 *  pinned the first resolved patient for the life of the mount, so a host that
 *  switched profile or started impersonating kept matching against the patient
 *  loaded at mount time. That is a wrong-patient display, not a stale render.
 *
 *  `undefined` means "the host said nothing"; `null` is a real value the host
 *  can pass to mean "no patient", so the two are not collapsed.
 */
export function selectPatientInfo(
  propPatientInfo: PatientInfo | null | undefined,
  load: PatientLoad,
): PatientInfo | null | undefined {
  if (propPatientInfo !== undefined) return propPatientInfo;
  return load.status === "ready" ? load.patientInfo : undefined;
}

/** Whether the bridge should resolve the signed-in patient itself.
 *
 *  Only when the host gave it somewhere to ask and has not answered the
 *  question already — an explicit `patientInfo` or `personId` means the host
 *  owns the resolution and we must not make a second, contradictory one.
 */
export function shouldResolvePatient(props: {
  ctomopBaseUrl?: string;
  patientInfo?: PatientInfo | null;
  personId?: string | number | null;
}): boolean {
  return (
    Boolean(props.ctomopBaseUrl) &&
    props.patientInfo === undefined &&
    // `== null` on purpose, and asymmetric with `patientInfo` above. A host
    // that is not TypeScript — which is the whole reason this bridge exists —
    // writes `personId: pid ?? null`, and there is no useful distinction
    // between "no person id" and "a null person id". `patientInfo` does have
    // one: `null` is the answer "this patient has no profile".
    props.personId == null
  );
}

/** Which of the bridge's four screens to show.
 *
 *  Extracted for the same reason as the two decisions above: there is no jsdom
 *  or @testing-library here, so a branch left inside the component is a branch
 *  nothing checks — and one of these branches is what a patient with no
 *  profile sees.
 */
export function selectBridgeView(args: {
  shouldLoad: boolean;
  load: PatientLoad;
  patientInfo: PatientInfo | null | undefined;
  personId?: string | number | null;
}): "loading" | "error" | "no-profile" | "matches" {
  const { shouldLoad, load, patientInfo, personId } = args;

  // `idle` while a resolution is due is the render between the host dropping
  // its own patient and the effect starting ours. Treating it as anything
  // else flashes the developer notice below for one frame.
  if (load.status === "loading" || (shouldLoad && load.status === "idle")) {
    return "loading";
  }
  if (load.status === "error") return "error";

  // `null` is the answer "this patient has no profile", whether the bridge
  // resolved it or the host stated it. `undefined` means nobody answered,
  // which is a host wiring mistake and keeps TrialMatches' developer notice.
  if (patientInfo === null && personId == null) return "no-profile";

  return "matches";
}

/** What the bridge believes about the current route and signed-in user. */
export interface BridgeSession {
  /** Everything about *where* it is asking. */
  key: string;
  /** Who it is asking as. Compared by identity — it may be a function. */
  signal: unknown;
}

/** Decide whether a render has to drop the resolved patient because it belongs
 *  to a different route or a different signed-in user. Returns `null` when
 *  nothing changed.
 *
 *  This is the check that stops one user's matches from rendering for the next
 *  one, so it is a pure function with tests rather than a condition buried in a
 *  component that has no test harness. The caller applies the result during
 *  render, not from an effect: an effect runs after the commit, which is one
 *  painted frame of the previous patient's data too late.
 */
export function nextSessionState(
  prev: BridgeSession,
  next: { routeKey: string; sessionSignal: unknown; shouldLoad: boolean },
): { session: BridgeSession; load: PatientLoad } | null {
  // `Object.is`, not `===`: a `NaN` signal never equals itself, and this runs
  // during render — a reset on every pass with unchanged props is React's
  // "Too many re-renders", i.e. the remote's whole subtree dying inside the
  // host page.
  if (prev.key === next.routeKey && Object.is(prev.signal, next.sessionSignal)) {
    return null;
  }
  return {
    session: { key: next.routeKey, signal: next.sessionSignal },
    load: next.shouldLoad ? { status: "loading" } : { status: "idle" },
  };
}

/** What identifies the signed-in user for resolution purposes.
 *
 *  A host passes `sessionKey` to say "this is the user"; without one, the
 *  identity of `getToken` is the only signal available and is used instead —
 *  conservative, since a change re-resolves.
 *
 *  The rejected spellings all come from the same place: these hosts have no
 *  types, so `auth.userId ?? null`, `user?.id ?? ""`, `Number(sub)` on a
 *  non-numeric id and `auth.ready && auth.userId` are all things they write.
 *  Read literally, each pins the signal to one value for every user and
 *  silently disables the guard — and the boolean and empty-string ones are the
 *  quietest, because a stuck signal never thrashes and so never warns either.
 *  `NaN` is worse in the other direction: it never equals itself, so it resets
 *  forever. None of them is a real user id, so none is treated as one.
 *
 *  The parameter is `unknown` on purpose. The type says `string | number |
 *  null`, but the hosts this exists for have nothing enforcing that, and a
 *  guard that only holds when the caller was already correct is not a guard.
 *  An object still passes through: it thrashes rather than sticking, and the
 *  bridge has a warning that names that case.
 */
export function resolveSessionSignal(sessionKey: unknown, getToken: unknown): unknown {
  if (sessionKey == null || sessionKey === "") return getToken;
  if (typeof sessionKey === "boolean") return getToken;
  if (typeof sessionKey === "number" && Number.isNaN(sessionKey)) return getToken;
  return sessionKey;
}

/** Whether `sessionKey` was usable, i.e. whether the signal above is a real
 *  session key or the `getToken` fallback. */
export function hasUsableSessionKey(sessionKey: unknown): boolean {
  return resolveSessionSignal(sessionKey, undefined) !== undefined;
}
