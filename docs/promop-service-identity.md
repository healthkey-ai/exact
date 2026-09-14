# EXACT's PRomop credentials and service identity

PRomop issues a separate bearer token per calling service (`SERVICE_AUTH_TOKENS`)
and no longer honours an unsigned `actor_iss`/`actor_sub` claim presented
alongside a service credential — including on OAuth client-credentials calls
(promop [#147](https://github.com/healthkey-ai/promop/issues/147),
[#568](https://github.com/healthkey-ai/promop/issues/568), PR #1218). EXACT's
service identity is `urn:service|exact`.

This page is the audit that migration asked for: every way EXACT can reach
PRomop, which credential each path uses, and what the code now guarantees.
Tracked as [#448](https://github.com/healthkey-ai/exact/issues/448).

## What EXACT asks PRomop for

| Path | Credential | Calls | Data |
|---|---|---|---|
| `trials/services/patient_info/promop_client.py` | `PROMOP_OAUTH_CLIENT_ID`/`_SECRET` (scope `patient/*.read`), or the static bearer `PROMOP_SERVICE_TOKEN` | `GET /api/v1/patient-records/{person_id}/`, plus `POST /o/token/` in OAuth mode | one patient row |
| `vocab_mirror/promop_vocab_client.py` | `PROMOP_VOCAB_OAUTH_CLIENT_ID`/`_SECRET` (scope `system/*.read`) | `GET /api/v1/vocab-releases/latest/`, `.../snapshot/{table}/` | vocabulary, no PHI |

Both reach v1 only; the deprecated `/api/patient-info/` prefix was dropped in
[#387](https://github.com/healthkey-ai/exact/issues/387). They are **two
distinct service credentials with two distinct scopes** and are not
interchangeable — a grant covering only `patient/*.read` leaves the vocab mirror
unable to sync (it fails closed with `VocabSyncError`).

### Findings

- **EXACT never claims a user to PRomop.** No `actor_iss`, no `actor_sub`, no
  token pass-through, no token exchange — the two clients send `Accept` and
  `Authorization` and no request body at all. Pinned by
  `tests/services/patient_info/test_promop_client.py::TestNoAssertedUserIdentity`.
- **EXACT writes nothing to PRomop.** The only non-GET is the OAuth token POST.
  None of the four endpoints the migration calls out —
  `/api/lab-results/sync/`, `/api/fhir/sync/`, `/api/persons/find_or_create/`,
  `/api/v1/patients/signup/` — is called from anywhere in this repository.
  EXACT's grant should therefore be **read-only**: `patient/*.read` for the
  patient client and `system/*.read` for the vocab mirror, with no
  `patient/*.write`.
- **User-attributed reads do not happen here by design.** Production patient
  context arrives as an inline `patient_info` payload that the federation host
  fetched from PRomop `/patient-info/me/` under the end user's *own* token. The
  server-side `?person_id=` route is gated off outside local/DEBUG
  (`EXACT_ALLOW_PERSON_ID_LOOKUP`, #150/#108) and stays that way: re-opening it
  needs a verified delegated-user identity reaching PRomop, which the removal of
  unsigned actor claims makes *more* restrictive, not less. See the
  authorization-boundary docstring in
  `trials/services/patient_info/resolve.py`.
- **`SERVICE_AUTH_TOKEN` in `accounts/authentication.py` is unrelated.** That is
  the token *callers* present **to** EXACT. Same name as PRomop's setting,
  opposite direction.
- **The dev harness is not a service caller.** `frontend/src/dev/promopClient.ts`
  logs into PRomop with a browser session cookie and is excluded from the
  federation bundle (`rollupOptions.input: {}` in `vite.remote.config.ts`).

### Out of band: direct database access

Several analysis commands read PRomop's `patient_info` table **directly over
psql** using `PATIENT_DATABASE_URL`, bypassing the API, the service identity and
PRomop's audit trail entirely: `fetch_exact_for_patients`,
`search_trials_for_patients`, `explain_trial_match`, `compare_status_equivalence`,
`compare_trials`, `evaluate_ethalon_live`, `probe_eligibility`, and
`docker/init_patients_db.sh` (which restores a dump from
`PATIENT_DATABASE_BACKUP_URL`).

These are developer/evaluation tooling, not the deployed request path, and
PRomop's enforcement release does not constrain them. They are named here so
the credential inventory is complete: retiring the shared API token does not
retire this channel, and the database credential deserves its own review.

## Fail-closed rules the code now enforces

- No usable credential ⇒ **no request**, in every mode. An empty
  `PROMOP_SERVICE_TOKEN` used to produce an anonymous `GET` that put a
  `person_id` on PRomop's wire under no identity at all; it now returns `None`
  without touching the network.
- A **half-configured** OAuth pair (exactly one of id/secret) is a hard
  misconfiguration, not a fallback trigger. It previously dropped to the static
  token — under per-service credentials, a dropped secret would silently
  substitute one service identity for another, which is precisely the
  shared-credential behaviour this migration removes.
- The same rule applies to the vocab client, which raises `VocabSyncError`
  before requesting a token rather than posting half a credential.

## Rollout notes

1. **Determine what the deployment actually uses before installing anything.**
   OAuth wins whenever both OAuth settings are present, so installing a new
   `PROMOP_SERVICE_TOKEN` on an OAuth-configured deployment changes nothing.
   Pick one credential per client and keep the other unset.
2. **Cutover needs a plan, not just a new value.** `PROMOP_SERVICE_TOKEN` holds
   a single token; overwriting it retires the old credential at that instant.
   Verify the new credential against staging before the swap.
3. **OAuth access tokens are cached per process** for their stated lifetime,
   keyed by `(token_url, client_id, scope)` — the secret is not part of the key.
   After rotating a client secret, a running process keeps using its cached
   access token until expiry; restart the workload if the rotation must be
   immediate.
4. **Per-runtime, not per-repo.** The web service, the vocab-sync job and ad-hoc
   management commands are separate workloads with different credentials. The
   migrate job uses neither client and should not receive patient credentials.
5. **Verify identity upstream.** After install, confirm with PRomop's
   audit/provenance that reads arrive as `urn:service|exact` with the expected
   scopes; retire the shared credential only once its use disappears from logs.

Secrets never live in this repository. They come from the host platform's secret
store as environment variables — see [setup.md](setup.md).
