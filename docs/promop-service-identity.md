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
| `trials/services/patient_info/promop_client.py` | `PROMOP_SERVICE_TOKEN` (the named token), or `PROMOP_OAUTH_CLIENT_ID`/`_SECRET` | `GET /api/v1/patient-records/{person_id}/`, plus `POST /o/token/` in OAuth mode | one patient row |
| `vocab_mirror/promop_vocab_client.py` | `PROMOP_VOCAB_SERVICE_TOKEN` (the same named token), or `PROMOP_VOCAB_OAUTH_*` | `GET /api/v1/vocab-releases/latest/`, `.../snapshot/{table}/` | vocabulary, no PHI |

Both service clients reach v1 only; the deprecated `/api/patient-info/` prefix
was dropped in [#387](https://github.com/healthkey-ai/exact/issues/387). Each
client reads its own credential settings, so the two *can* hold different
credentials — but they should hold the same one, for the reasons below.

## The credential EXACT should use, and why

**The named static bearer, for both clients, granted `patient/*.read` and
nothing else.** This is settled by PRomop's code rather than by preference:

- **Only the static-bearer path produces `urn:service|exact`.**
  `ServiceTokenAuthentication` matches the bearer against a managed application
  token (or `SERVICE_AUTH_TOKENS`) and builds the principal from the matched
  credential's service id: `Identity.objects.get_or_create(issuer='urn:service',
  sub=matched.service_id)`. An OAuth2 client-credentials token authenticates
  through the OAuth path instead, where the principal is whatever user its
  `Application` is bound to — not something the service-application admin
  manages. Since the identity in PRomop's audit trail is the point of this
  migration, the transport that produces it is the one to use.
- **One token for both clients, so EXACT is one principal.** Keeping the
  vocabulary mirror on OAuth would put one logical service into PRomop's audit
  under two identities. `PROMOP_VOCAB_SERVICE_TOKEN` is a separate setting from
  `PROMOP_SERVICE_TOKEN` — set both to the same value — so the two clients stay
  configurable apart without one module reading the other's settings.
- **`patient/*.read` is sufficient for every endpoint EXACT calls.** The
  vocabulary endpoints use `VocabReadPermission`, whose
  `read_scopes = {patient/*.read, user/*.read, system/*.read}` — `system/*.read`
  is *additionally accepted*, not required. The patient route
  (`PatientRecordV1ViewSet`, verified: `permission_classes =
  [ScopedTokenPermission, PatientSelfScopePermission]`) grants safe methods on
  `patient/*.read`, and a service token bypasses the object-ownership check. All
  three calls EXACT makes are GETs.
- **Do not leave both configured.** OAuth wins whenever both OAuth settings are
  present, in both clients — a deployment configured one way would behave the
  other. And the OAuth access-token cache is keyed without the client secret, so
  a rotated secret keeps working until the cached token expires.

The one thing OAuth would have been better at: a static bearer travels on every
request, while an OAuth client secret only ever reaches the token endpoint. That
is a real trade, made knowingly in exchange for the audit identity.

### Findings

- **EXACT never claims a user to PRomop.** No `actor_iss`, no `actor_sub`, no
  token pass-through, no token exchange — the two clients send `Accept` and
  `Authorization` and no request body at all. Pinned by
  `tests/services/patient_info/test_promop_client.py::TestNoAssertedUserIdentity`.
- **Neither service client writes to PRomop.** Their only non-GET is the OAuth
  token POST. (The browser dev harness does POST `/api/auth/login/` and
  `/api/auth/logout/` — a session login, under no service credential; see
  below.) None of the four endpoints the migration calls out —
  `/api/lab-results/sync/`, `/api/fhir/sync/`, `/api/persons/find_or_create/`,
  `/api/v1/patients/signup/` — is called from anywhere in this repository.
  EXACT's grant should therefore be **read-only**, and one scope covers
  everything it calls: `patient/*.read`, with no `patient/*.write`.
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
  logs into PRomop with a browser session cookie (hence its two POSTs) and is
  excluded from the federation bundle (`rollupOptions.input: {}` in
  `vite.remote.config.ts`). It is also the one in-repo caller still on the
  `/api/patient-info/` prefix, whose stated sunset (2026-09-01) has passed —
  worth moving to v1 independently of this migration, since a dev-only tool is
  not covered by the service-identity work either way.

### Out of band: direct database access

Six analysis commands read the `patient_info` table **directly over psql**
using `PATIENT_DATABASE_URL`, bypassing the API, the service identity and
PRomop's audit trail entirely: `fetch_exact_for_patients`,
`search_trials_for_patients`, `explain_trial_match`, `compare_status_equivalence`,
`compare_trials`, `probe_eligibility`. Separately, `docker/init_patients_db.sh`
*populates* a local patients database: it probes `public.patient_info` for rows
(a guard against restoring over a populated database), then drops the public
schema and restores a dump downloaded from `PATIENT_DATABASE_BACKUP_URL` — so
that URL is a third credential-bearing setting alongside the database URL
itself.

These are developer/evaluation tooling, not the deployed request path, and
PRomop's enforcement release does not constrain them. Retiring the shared API
token does not retire this channel, and the database credential deserves its own
review.

Two limits on this inventory, stated so it isn't read as more exhaustive than it
is. Some evaluator commands are git-ignored (`.gitignore`: `evaluate_ethalon_live`
and its test) and therefore outside anything a tree search can check, though they
read the same patient data. And `compare_trials` carries a second live service
credential that is not PRomop's at all: a CancerBot API token, read from its
input file and sent as `Authorization: Token …` to `app.cancerbot.org`.

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
- Failing closed on the credential must not fail *open* on the answer. A
  `person_id` that can't be fetched now raises `PatientContextUnavailable`
  (502) instead of resolving to "no patient" — a patientless search returns the
  whole corpus, unscored and unfiltered, which looks like a valid result in a
  clinical matcher (#156). Without this, a dropped client secret would answer
  200 with every trial we know. The same status reaches every client: the
  failure is memoized per request, and the browsable renderer's second pass —
  which runs after the error has already become the response — is answered with
  "no patient" rather than a second raise, which would escape rendering and
  report a 500.

## Rollout notes

1. **Install the named token as `PROMOP_SERVICE_TOKEN` *and*
   `PROMOP_VOCAB_SERVICE_TOKEN`, and clear both OAuth pairs.** Leaving OAuth
   configured silently keeps the old transport: it wins whenever both of its
   settings are present.
2. **Rotation is a PRomop-side operation, not an env-var swap.** An application
   may hold several active tokens at once, so the safe order is: create the
   replacement, deliver it, update EXACT's settings, then revoke the old token.
   Overwriting the variable alone would otherwise retire the old credential at
   that instant. Revocation and scope changes take effect on the next request.
3. **OAuth access tokens are cached per process** for their stated lifetime,
   keyed by `(token_url, client_id, scope)` — the secret is not part of the key.
   After rotating a client secret, a running process keeps using its cached
   access token until expiry; restart the workload if the rotation must be
   immediate.
4. **Per-runtime, not per-repo.** The web service, the vocab-sync job and ad-hoc
   management commands are separate workloads with different credentials. The
   migrate job uses neither client and should not receive patient credentials.
5. **Three smoke GETs before retiring anything**, one per endpoint EXACT
   actually calls: a patient record, `vocab-releases/latest`, and one snapshot
   table. The permission classes above were read from source, not exercised
   against the deployment.
6. **Verify identity upstream.** Confirm with PRomop's audit/provenance that
   reads arrive as `urn:service|exact` with the expected scopes; retire the
   shared credential only once its use disappears from logs.

Secrets never live in this repository. They come from the host platform's secret
store as environment variables — see [setup.md](setup.md).
