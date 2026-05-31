# EXACT frontend (`exact_remote`)

Federated React 19 remote that ships the `TrialMatches` component. Toolchain mirrors SoC / ctomop / hk-labs so a host can mount EXACT alongside the other org remotes with one React tree + one TanStack QueryClient.

This package was scaffolded in #103 (part of #101). The real component lands in #104.

## Scripts

| Script | What |
|--------|------|
| `npm run dev` | SPA harness for iterating on the component in isolation. Serves `index.html` at http://localhost:5178/. Proxies `/api/*` → Django on :8000. |
| `npm run dev:remote` | Federation dev server. Serves `dev-harness.html` at http://localhost:5177/ and exposes the remote at `/remoteEntry.js`. Adds CTOMOP proxies (`/ctomop-local`, `/ctomop-staging`) with Set-Cookie rewriting. |
| `npm run build` | Production federation build. Output: `dist/remote/remoteEntry.js` + exposed modules. |
| `npm run build:spa` | Production SPA build. Output: `dist/`. Use this if you want to deploy the harness as a static site. |
| `npm run typecheck` | `tsc -b`. CI gates this. |

## Exposed modules

```ts
// In the host:
import("exact_remote/TrialMatches").then(({ TrialMatches }) => { /* … */ });
import("exact_remote/types").then(({ /* TrialMatchesProps, … */ }) => { /* … */ });
```

The host must provide:

- An `axios.AxiosInstance` with `baseURL` (typically `/api`) and `Authorization: Token <…>`.
- A TanStack `QueryClient` (optional — the component spins up its own otherwise).
- Either a CTOMOP `personId` or an inline `patientInfo` payload (mutually exclusive; `patientInfo` wins to match the server-side `resolve_patient_info` precedence).

## CSS token contract

Tokens live on `.exact-root` scoped to the remote. Hosts can override any `--exact-*` on `:root` to map them to the host design system (see `frontend/src/federation/exact.css`). Per the hk-labs `docs/module-federation.md` namespace convention.

`assertExactTokens()` returns the list of unset required tokens for dev-time sanity checks — call from the harness to catch missing host overrides.

## Environment variables

| Name | Default | What |
|------|---------|------|
| `VITE_EXACT_API_PROXY_TARGET` | `http://localhost:8000` | Where the `/api` proxy points (EXACT's Django). |
| `VITE_CTOMOP_LOCAL_TARGET` | `http://localhost:8001` | Local CTOMOP for the federation dev harness. |
| `VITE_CTOMOP_STAGING_TARGET` | `https://ctomop.onrender.com` | Staging CTOMOP for the federation dev harness. |
| `VITE_EXACT_TOKEN` | _unset_ | DRF token for local dev. Real login flow lands in #104. |

## Status

- ✅ #103 — Scaffold + federation config + CSS token contract + dev harness skeleton.
- ⏳ #104 — `TrialMatches` body (filters, eligibility grouping, distance sort, trial detail) + CTOMOP picker + token login flow.
- ⏳ #101 — Closes when #104 ships.
