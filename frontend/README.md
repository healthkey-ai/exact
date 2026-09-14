# EXACT frontend (`exact_remote`)

Federated React 19 remote that ships the `TrialMatches` component. Toolchain mirrors SoC / ctomop / hk-labs so a host can mount EXACT alongside the other org remotes with one React tree + one TanStack QueryClient.

## Scripts

| Script | What |
|--------|------|
| `npm run dev` | SPA harness for iterating on the component in isolation. Serves `index.html` at http://localhost:5178/. Proxies `/api/*` → Django on :8000. Use this to develop the component against an EXACT instance without CTOMOP. |
| `npm run dev:remote` | Federation dev server with the full CTOMOP picker → TrialMatches flow. Serves `dev-harness.html` at http://localhost:5177/ and exposes the remote at `/remoteEntry.js`. Adds CTOMOP proxies (`/ctomop-local`, `/ctomop-staging`) with Set-Cookie rewriting. |
| `npm run build` | Production federation build. Output: `dist/remote/remoteEntry.js` + exposed modules. |
| `npm run build:spa` | Production SPA build. Output: `dist/`. Use this if you want to deploy the harness as a static site. |
| `npm run typecheck` | `tsc -b`. CI gates this. |

## Dev harness flow (`npm run dev:remote`)

1. **EXACT sign-in** — `POST /api-token-auth/` with username + password returns a DRF token. Stored in `localStorage` so a refresh keeps the session. Can also be skipped by setting `VITE_EXACT_TOKEN` in `.env.local`.
   - Sign-out clears the local token and calls `CtomopClient.logout()` (tolerant of 401/403), but does **not** revoke the EXACT token server-side — DRF's default token model has no revoke endpoint. Rotate via `manage.py drf_create_token <user>` if needed.
2. **CTOMOP source toggle** — local (`/ctomop-local`) vs staging (`/ctomop-staging`). The dev proxy rewrites Set-Cookie so the CTOMOP session is scoped to its mount path and doesn't bleed into other localhost services.
3. **CTOMOP login (inline)** — when the patient-list call returns 401/403, the picker surfaces an inline form for `POST /api/auth/login/` (CTOMOP's Django session login). No EXACT token mixing — separate axios instances.
4. **Patient picker** — list of `{person_id, patient_name, disease, …}` summaries from `/api/patient-info/`. Click a row to pick.
5. **TrialMatches mount** — federated component receives the EXACT axios instance + `personId`. EXACT's resolver (#102) fetches the CTOMOP row server-side via the `?person_id=` path.

## Exposed modules

```ts
// In the host:
import("exact_remote/TrialMatches").then(({ TrialMatches }) => { /* … */ });
import("exact_remote/TrialMatchesBridge").then((mod) => { /* … */ });
import("exact_remote/types").then(({ /* TrialMatchesProps, TrialMatchesBridgeProps, … */ }) => { /* … */ });
```

### `./TrialMatches` — React hosts

The host must provide:

- An `axios.AxiosInstance` with `baseURL` (typically `/api`) and `Authorization: Token <…>`.
- A TanStack `QueryClient` (optional — the component spins up its own otherwise).
- Either a CTOMOP `personId` or an inline `patientInfo` payload (mutually exclusive; `patientInfo` wins to match the server-side `resolve_patient_info` precedence).

### `./TrialMatchesBridge` — hosts that are not React

HealthTree ONE is SvelteKit, so it mounts through the provider contract instead.
Props are data-only: the host passes `baseUrl` + `getToken` rather than a live
axios instance, so it needs neither axios nor react-query.

```ts
const { default: provider } = await loadRemote("exact_remote/TrialMatchesBridge");

await provider().render({
  dom,                                     // the container element
  baseUrl: "https://exact.example",        // EXACT origin
  apiBasePath: "/api",                     // defaults to "" — pass it if the
                                           // API is not at the origin itself
  ctomopBaseUrl: "https://promop.example", // optional — see below
  ctomopApiBasePath: "/api",               // optional, defaults to "/api"
  getToken,                                // any function; see sessionKey below
  sessionKey: auth.userId,                 // change it when the user changes
});

provider().destroy({ moduleName: "exact_remote/TrialMatchesBridge", dom });
```

The bridge sends `Authorization: Bearer <token>` — note this differs from the
`Token <…>` scheme the `./TrialMatches` section above documents for the axios
instance a React host builds itself.

`provider()` is memoised on purpose: bridge-react keys its mounted roots in a
map private to each factory call, so a `render` and a `destroy` that went
through two different calls would unmount nothing.

Patient resolution:

- Pass `patientInfo` or `personId` and the host owns it — the bridge does not
  fetch. An explicit prop wins on every render, including after a resolution
  has already happened, so switching profile or starting impersonation takes
  effect immediately.
- Pass `ctomopBaseUrl` and nothing else, and the bridge resolves the signed-in
  patient itself: PRomop's `/patient-info/me/`, then EXACT's
  `/normalize-ctomop-row/`. It must be the caller's own token — EXACT's
  server-side `person_id` resolver is disabled by default because its service
  token is not bound to the caller (IDOR), so fetching "me" is what makes
  PRomop enforce access. The same token goes to both origins, so it has to be
  valid at both.
- Pass neither and you get the same "nothing to match against" state as
  `./TrialMatches` on its own.

`sessionKey` identifies the signed-in user: change it and the bridge re-resolves,
keep it and the bridge holds what it has. It must be a scalar that is stable
within a session; `null`, `""`, `NaN` and booleans are treated as "not passed"
(they are what `?? null`, `?? ""`, `Number(sub)` and `ready && id` produce when
there is nothing to read), and an object would be a new value on every render. Without one, `getToken`'s identity is
used as the signal instead — safe, but blunt, because a host that builds
`getToken` inline rebuilds it on every render and each rebuild sends the user
back to the loading state, losing their filters and the query cache. Pass a
`sessionKey` and `getToken` can be as unstable as you like.

Either way the bridge must be able to tell sessions apart: a mount reused
across a logout/login with no changing signal keeps showing the previous
user's matches.

### Cascade-layer contract (both exports)

The remote appends its stylesheet to the **host's** `<head>`, so the host
should declare the layer order before anything else:

```css
@layer properties, theme, base, components, mf-remote, utilities;
```

Everything this remote injects goes into `mf-remote`, which puts it above the
host's preflight (our screens still style themselves) and below the host's
utilities (we cannot restyle the host's chrome). A host that declares no order
still gets the layer — but note that unlayered host declarations then outrank
every rule of ours regardless of specificity, which is the trade this contract
makes on purpose. See `src/federation/cssLayer.ts`.

## CSS token contract

Tokens live on `.exact-root` scoped to the remote. Hosts can override any `--exact-*` on `:root` to map them to the host design system (see `frontend/src/federation/exact.css`). Per the hk-labs `docs/module-federation.md` namespace convention.

`assertExactTokens()` returns the list of unset required tokens for dev-time sanity checks.

## Environment variables

| Name | Default | What |
|------|---------|------|
| `VITE_EXACT_API_PROXY_TARGET` | `http://localhost:8000` | Where the `/api` proxy points (EXACT's Django). |
| `VITE_CTOMOP_LOCAL_TARGET` | `http://localhost:8001` | Local CTOMOP for the federation dev harness. |
| `VITE_CTOMOP_STAGING_TARGET` | `https://ctomop.onrender.com` | Staging CTOMOP for the federation dev harness. |
| `VITE_EXACT_TOKEN` | _unset_ | DRF token. When set, skips the EXACT login form. |
| `VITE_CTOMOP_BASE` | `/ctomop-local` | Override the default CTOMOP proxy mount used by `CtomopClient`. |

## Status

- ✅ #103 — Scaffold + federation config + CSS token contract.
- ✅ #104 part 1 (PR #114) — `TrialMatches` body: filters, eligibility grouping, distance sort, inline detail.
- ✅ #104 part 2 (this PR) — CTOMOP picker + EXACT token-login flow + dev harness wiring.
- ✅ #101 — closed when this PR merges.
