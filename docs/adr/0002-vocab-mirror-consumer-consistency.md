# ADR 0002: Consuming the promop vocabulary mirror with client-side consistency

- **Status:** Proposed
- **Date:** 2026-07-29
- **Deciders:** EXACT team; the mirror direction is promop's call (Adam Blum). The
  client-side consistency design here is EXACT's.
- **Reviews:** Codex (design pass) + gstack `/plan-eng-review` (eng-manager pass) —
  both folded in (right-sizing: defer partitions / REPEATABLE-READ / the full
  versioned CB-projection contract; keep the immutable-generation + pointer-flip +
  per-request-pin + fail-closed core).
- **Relates / supersedes:** reverses the **access mechanism** of epic #234 (per-request
  concept-graph API + result cache); depends on **promop ADR 0001** (*promop is the
  vocabulary source of truth*, to be revised from "API + cache" to "release-pinned
  mirror") and **promop [#334](https://github.com/healthkey-ai/promop/blob/dev/docs/vocab-consumer-cache-protocol.md)**
  (vocabulary consumer cache protocol). Does **not** change [ADR 0001](0001-cross-vocabulary-mapping.md)
  (already Superseded) or the [cb_code↔concept_id exception](../omop/cb-code-concept-id-exception.md).

## Context

promop is the single source of truth for OMOP vocabulary. The **access mechanism** has
changed: instead of EXACT querying promop's concept-graph API per request and caching
per-source results (epic #234, "API + cache"), EXACT will keep a **release-pinned local
mirror** of the OMOP vocabulary tables it needs (`concept`, `concept_relationship`,
`concept_ancestor`, `vocabulary`) and traverse the graph locally, syncing via promop #334
(poll `vocab-releases/latest` + ETag, download NDJSON snapshots, verify, load).

**Why the mirror (the real reason):** *client-side whole-vocab consistency.* A
per-request / per-source result cache cannot guarantee that the union of everything it has
cached reflects **one coherent vocabulary release** — different entries can be fetched at
different times across a release boundary, giving a partial, drifting, non-reproducible
view. A full mirror of a single ETag'd release gives every client-side OMOP-derived
computation one **consistent, complete, versioned, reproducible** (and offline-capable)
view.

**Scope of that guarantee (do not overstate it):** it covers **OMOP-derived graph
expansion** only. EXACT's therapy *types* and *planned therapies* stay in CB-code space,
outside the OMOP mirror; and the mirror alone does not map CB `code`→`concept_id`
(`TherapyOmopMapping`) or CB drug-class *category* policy (`ComponentCategoryOmopLookup`).
"One release for the whole match" is real **only if** the CB crosswalk and the trial
`omop_*` projection pin the **same** release as the mirror (see Decision §Cross-artifact).

**The problem the mirror introduces.** A naive mirror consumer trades promop-side freshness
coupling for **new client-side inconsistency modes**: torn / half-loaded / mixed-release
reads while a sync is in flight, a release swapping mid-match, silent staleness when sync
fails, and cross-artifact drift (mirror at release R, crosswalk/projection at R-1). This
ADR records the consumer-side design that prevents them. (Note: the concept-graph path is
built but **not yet wired** into matching — current OMOP mode is a direct `concept_id`
overlap against materialized trial columns — so these guarantees become load-bearing when
Phase-T graph expansion lands, and are designed in now so we don't ship a footgun.)

## Decision

Adopt the release-pinned mirror **with the client-side consistency design below.**

**Principle.** *Immutable, verified, release-pinned generations + an atomic pointer flip +
"read the active release once per request" + fail-closed on stale/mismatch.* Reads need
**no locks** (immutability + explicit release pinning + MVCC do the work); a lock is used
only by the **singleton sync writer** and the **brief pointer-flip** transaction.

```
                     promop  /api/v1/vocab-releases/latest/  (+ ETag, #334)
                                        │  poll (Cloud Run Job, advisory-locked singleton)
                                        ▼
   ┌──────────────── sync ────────────────────────────────────────────────┐
   │ download NDJSON  →  STAGING / inactive generation (release_id = R)     │
   │ verify: __done sentinel + row_counts + checksums  (fail → discard R)   │
   │ release-match gate: mirror, crosswalk, component-lookup, projection = R?│
   └───────────────────────────────┬───────────────────────────────────────┘
                                    │ all verified → ONE short txn
                                    ▼
                        active_release: Rprev  ──flip──▶  R      (atomic)
                                    │
   readers (gunicorn workers, no lock) ──────────────────────────┐
     request start: read active_release ONCE → pin release_id=R  │
     every query filters release_id=R (immutable rows)           │
     Rprev kept for a retention window so in-flight runs finish  │
     no verified release / age > MAX  →  503, never a live/other │
                                    ────────────────────────────┘
```

### Consumer-side mechanisms (risk → guard)

| # | Inconsistency risk | Guard |
|---|---|---|
| 1 | Torn / half-loaded read during sync | Load into **staging / inactive generation**; never mutate the active data in place (no `TRUNCATE`+reload on live tables). Activate by an **atomic pointer flip**. |
| 2 | Mixed-release read (table A from R, B from R-1) | Single **`active_release` pointer** + a `release_id` column on every row (immutable per generation); readers **pin `release_id=R`** for all reads. |
| 3 | Release swaps mid-match | Read the active release **once at run start** and pin **all OMOP-mirror reads** in that run to the chosen `release_id` (wrap the request, not just the graph-expansion call). A **retention window** keeps the previous generation alive so in-flight runs finish on their release. (READ COMMITTED + pin + retention is sufficient; REPEATABLE READ is **not** required.) Binding the *non-mirror* artifacts (crosswalk / projection) to the same release is #4. |
| 4 | Cross-artifact drift (mirror R vs crosswalk/lookup/projection R-1) | **Release-match gate** at activation. The `default`-DB artifacts are checked in-transaction: the mirror generation is populated, and the `TherapyOmopMapping` crosswalk + `ComponentCategoryOmopLookup` are stamped for R + covered (the component-lookup check ships in #262; the crosswalk follows the same stamp+coverage pattern). The trial **`omop_*` projection lives on the CB-owned trials DB**, gated via a release-keyed **attestation**: CB stamps every trial with its projection release, validates the whole batch, and publishes a "projection ready for R" attestation the activation gate reads. **Transport ratified by [ADR 0003](0003-cb-exact-convergence-attestation-seam.md) Option D (2026-08-07): the attestation is immutable/insert-only on the CB-owned trials DB and EXACT reads it cross-DB (a monotonic, release-keyed row → a lagging read only ever fail-closes) — superseding this row's original "publish into EXACT's `default` DB" framing (that kept the flip atomic on one DB but is now retired with #307).** The gate VERIFIES the attestation checksum vs a local recompute (§Gate 1 gap #1), not mere existence. Mismatch → do not activate, keep the last consistent bundle, alert. Provenance + attestation are a CB contract (CB `docs/exact-downstream-and-omop.md`; CB epic cancerbot-org/cancerbot#4653); the EXACT attestation model + gate are #265, **observe-only until the ratified enforce trigger is met** (ADR 0003 / §Gate 1 gap #2: Option-D read path live + checksum-verify (gap #1) + revocation safeguards (gap #2) + `type_release_gate` on the verdict path — ADR 0004 retired Phase-T as the trigger). Coverage (projection concept_ids ⊆ R) is defense-in-depth, **not** provenance. |
| 5 | Silently serving a stale mirror | **Fail-closed + `MAX_MIRROR_AGE`:** serve the active release only up to an age bound; past it return **503 `vocabulary_release_unavailable`**. Never fall back to a live API or a different release. |
| 6 | Partial / truncated download accepted as complete | Verify #334's completeness signals — **`__done` sentinel + `row_counts` + `checksums`** — before a generation is `READY`; unverified never activates. **Do not advance the retry ETag** on mere observation (else a failed R becomes a `304` and never retries). |
| 7 | Concurrent / duplicate sync writers | **Singleton writer:** a Postgres **advisory lock** for the whole sync run (no Redis needed); sync runs as a **Cloud Run Job**, never in a request. Lock held → exit "another sync in progress." |
| 8 | Per-process caches drift across workers | Key any in-process cache (e.g. `ComponentCategoryOmopLookup`'s LRU) by `release_id` and flush on release change, or replace with release-pinned queries. |
| 9 | No visibility into which release a match used | Stamp `release_id` in match metadata + an **`X-Exact-OMOP-Release`** response header; log per match; emit metrics for active/latest/age/sync-state/mismatch-rejections. |

### What lands now (v1) vs deferred

- **v1 (safe core):** #1–#3 (immutable generation-tagged tables + `active_release` pointer
  flip + per-request pin + retention window), #5, #6, #7, #8, #9, and #4 in its **light**
  form (a release-match gate on a single metadata value).
- **Deferred (v2 / tie to Phase-T):**
  - **List-partitioning by `release_id`** — a retirement/bloat optimization, not a
    correctness requirement; a generation-tagged table + composite index + retention window
    gives the same guarantee.
  - **REPEATABLE READ / cross-DB snapshot transactions** — unnecessary once generations are
    immutable and a retention window protects in-flight runs; also sidesteps the "no single
    atomic transaction across `default` + read-only `trials`" limitation.
  - **Full versioned CB trial-projection contract** (CB republishing an immutable
    `trial_omop_projection(release_id, trial_id)`) — a cross-team (Conway) change that would
    stall the mirror; v1 uses the release-match **gate** instead and defers full versioning
    until Phase-T actually consumes the mirror.

### Gate 1 — release-consistency for OMOP drug-class *type* matching (#286)

Mechanism #4 gates **trial↔mirror** at activation. OMOP *type* matching (promop ADR 0002)
adds a **patient** side: the patient carries pre-expanded HemOnc class `concept_id`s and one
aggregate `therapy_release_id` (promop VocabularyRelease pk as a decimal string; unanimous of
contributing therapy lines else null — promop [#394](https://github.com/healthkey-ai/promop/pull/394)).
This is EXACT issue [#286](https://github.com/healthkey-ai/exact/issues/286) "Gate 1", distinct
from Gate 2 (per-concept validity of the patient's own ids in the mirror). Codex-adjudicated.

**Architecture: extend mechanism #4's *role* (gate activation on a projection attestation) — do NOT
read per-trial release tags for the match-time `patient == trial` decision.** (gap #2's containment
option A separately reads the per-trial tag to *scope the projection*, a different use.) #4's
attestation transport is [ADR 0003](0003-cb-exact-convergence-attestation-seam.md) **Option D —
RATIFIED (2026-08-07): immutable / insert-only attestation on the CB-owned trials DB, read cross-DB;
#307 closed won't-do and the interim `publish_projection_attestation(...)` posture retired.** Gate 1
requires an **immutable, insert-only** attestation binding R to the projection checksum — the
Option-D relocation (moving the attestation off EXACT's `default` DB to the trials DB) is a
**precondition to enforcement** (gap #2 below).
If activating mirror release R requires the trial projection to be *verified complete
for R*, and the patient release == R, then patient and trial share generation R transitively —
a direct per-trial `patient == trial` compare adds nothing, and a per-trial `omop_therapy_digest`
mirrored onto EXACT is dead weight at match time (EXACT holds neither a patient digest nor an
independent expected digest). CB's per-trial `omop_therapy_release_id`+`omop_therapy_digest`
(cancerbot-org/cancerbot#4667) are **inputs** to CB's release-wide backfill + projection
attestation, not an EXACT match-time dependency.

**Match-time check (new — the only patient-side gate):** accept `patient.therapy_release_id`
**only** when it is a `str` of ASCII digits of bounded length, then compare the **canonical
decimal strings**: `patient.therapy_release_id == str(active_pinned_release())`. Comparing
strings (not `int(...)`) side-steps two `int()` coercion hazards — `int(True) == 1` /
`int(1.9) == 1` — that a bare `int(patient.therapy_release_id)` would accept whenever release 1 is
active. The comparison is **same-namespace by construction**: EXACT's mirror `release_id` *is* the
promop `VocabularyRelease` pk (`vocab_mirror.models`), the same identifier promop stamps into
`therapy_release_id`. **Fail-closed** on null / non-string / non-digit / over-length / mismatch, and
when `active_pinned_release()` is `None` (no active release); the string compare itself never
raises, so the length bound is defense-in-depth (it also caps the rejected-`int()` path's ~4300-digit
`ValueError`). Memoized once per request; applied at the same
three `resolve_type_validation` sites Gate 2 uses — queryset prefilter + the matcher's verdict
and status-render (display) paths — so a stale patient can't be *shown* matching. Gate 1 is
**not** redundant
with Gate 2: only a release check catches a changed drug→class `Is a` expansion that keeps
every concept valid but shifts the patient's aggregate class set — and thus the overlap.

**Two fail-OPEN gaps in mechanism #4 as prototyped — close BEFORE enforcing:**
1. **Existence-only attestation is fail-open — DONE (observe-only, #345).** `projection_attested(R)`
   proved only that a row was inserted, not that EXACT's trial rows are the complete CB projection
   for R. Activation now **verifies** the attestation `checksum`+`trial_count` against a hash EXACT
   **recomputes from its own trial rows** (`compute_trial_projection_checksum`: sha256 over every
   `Trial`, per-trial `[code, sorted(required), sorted(excluded)]`, `code`=CB-portable identity,
   tuples sorted in-process by codepoint — the frozen CB↔EXACT contract). Blank checksum → pending
   observe-only / fail-closed when enforced; mismatch → fail. Still observe-only until gap #2.
2. **Post-attestation trial mutation is fail-open** — resolution below (**hybrid B(b1) + A**,
   codex-adjudicated). Do not flip `_PROJECTION_GATE_ENFORCE` / `_PATIENT_RELEASE_GATE_ENFORCE`
   until it lands.

### Gate 1 gap #2 resolution — projection immutability + revocation (codex-adjudicated)

gap #1 verifies the projection at ACTIVATION; but EXACT's `omop_therapy_types_*` columns are
**mutated in place** (no per-release versioning), so after R is active a projection mutation drifts
the live projection from R's verified checksum WITHOUT re-running the gate — `active_release` stays R
and Gate 1 keeps trusting R. The required invariant:

> For every **enabled** active release R, the canonical projection EXACT can read equals
> `ProjectionAttestation[R].checksum`. Any change to a projected tuple **or that projection's
> membership** disables R *before* the changed state can be served —
> `enabled(R) ⇒ sha256({[code, sort(req), sort(exc)] | trial.omop_therapy_release_id = R}) = attestation[R].checksum`.

**Decision: hybrid — B(b1) as the correctness mechanism, A as defense-in-depth. Ship neither alone.**

- **B(b1) — revoke-on-change (the load-bearing gate).** A **mutable, one-way `disable(R)` revocation
  record**, SEPARATE from the immutable insert-only attestation (ADR 0003 decision-content item #7,
  revocation/rollback). CB
  writes `disable(R)` **atomically with any mutation/re-stamp that would alter R's attested tuple set
  or membership**; EXACT reads the revocation for its pinned release **on every request** and
  **fails closed** (no OMOP-type matching / 503) when disabled. **Topology + read-consistency.** There is
  **ONE authoritative, CB-owned trials store** (not an EXACT-side copy), with multiple accessors:
  EXACT is a **read-only** consumer (the ratified Option-D `trials` alias — a separate cross-DB
  connection; ADR 0003 rejected embedding the vocab seam, and in-process embedding is a matcher-only,
  not-yet-decided horizon — open-Q #2 — so correctness does NOT rest on it), while the prompts-admin
  zone (SME trial-attr edits) and the Airflow extractor are **writers**. The `disable(R)` record is
  **co-located with the projection on that store** and CB writes it **atomically with the mutation**.

  **Read requirement (unconditional):** the revocation⋈projection read MUST be **one MVCC snapshot
  that observes a single atomic commit** — either both `disable(R)` and the mutation, or neither
  (`REPEATABLE READ`, or one joined statement). And it must be **commit-current**: ADR 0003's
  lagging-replica tolerance is granted to the **monotonic attestation** only (a missed attestation
  merely *delays* activation = fail-closed); **revocation is non-monotonic**, so a read that misses a
  just-committed `disable(R)` (a lagging replica / stale connection) serves drifted data =
  **fail-OPEN**. EXACT's revocation read therefore **cannot use a lagging replica** — it reads
  commit-current or fails closed. **Deployment consequence:** if the Option-D `trials` alias is itself
  a lagging replica (which ADR 0003 permits for the attestation), the revocation⋈projection read needs
  a **distinct commit-current / primary read path** — the single replica-backed alias satisfies the
  attestation's replica-tolerance but NOT revocation's commit-currency. This also **rules out an
  EXACT-side / `default`-DB revocation list** (floated in earlier ADR 0003 drafts, superseded there):
  it cannot share the projection's snapshot.

  **Open (write-side — needs research):** the invariant "any projection-altering mutation atomically
  writes `disable(R)`" must hold across the **multiple writers** of the one TrialsDB — the
  prompts-admin zone and Airflow write directly, some bypassing any single app chokepoint. Enforcing
  atomic-disable-on-every-mutation **writer-agnostically** is unresolved (candidate: a **DB-level
  trigger** on the projection columns / release tag so the invariant holds at the data layer
  regardless of writer; vs a single enforced write-chokepoint + rejected direct writes). Tracked as
  separate research. b1's correctness is
  therefore **contingent on CB's atomic-disable discipline**; *b3* per-request full re-hash is too
  costly for the hot path BUT is the one mechanism that self-checks regardless of that discipline —
  keep it as a **periodic audit backstop** to catch a missed `disable(R)` (drift a missed disable
  would otherwise hide). *b2 polling* leaves a drift window = a clinical **fail-OPEN blocker**.
- **A — release-scoped read (containment, NOT sufficient alone).** EXACT **reads** CB's per-trial
  `omop_therapy_release_id` (#4667) from the one trials store (no EXACT-side copy) and scopes its type
  projection + the gap-#1 checksum to trials tagged `== active_release`. This stops serving a *re-stamped new tuple as R*, but **A alone
  is still fail-open**: a **missed re-stamp** leaves changed columns tagged R (served as R), and a
  re-stamp itself **changes R's membership** (the trial leaves the R-set) → silent drift. So A
  contains + validates but does **not** subsume revocation.

**Ownership.** CB: owns the projection, a **non-null** per-trial release tag + digest, the immutable
insert-only attestation, AND the mutable per-release disable record; must atomically disable active R
on any change to R's tuple set/membership. EXACT: read-only; pins R; (with A) scopes reads + the
activation checksum to `tag = R`; reads revocation every request and fails closed if disabled;
activation rejects checksum mismatch / null tag / already-revoked R. Both: define the canonical
population/checksum semantics + the read-consistency protocol.

**Minimal safe order:** (1) CB: atomic disable-on-projection-change + the revocation record; (2) EXACT:
per-request revocation enforcement, race-safe read ordering, fail-closed; (3) EXACT: the R-tag
read/filter + gap-#1 checksum over the identical R-scoped population + activation rejects
mismatch/null-tag/revoked-R; (4) exercise mutation / missed-stamp / re-tag / null-tag / concurrent-
reader; (5) ONLY THEN flip `_PROJECTION_GATE_ENFORCE` + `_PATIENT_RELEASE_GATE_ENFORCE` — and note
`_PROJECTION_GATE_ENFORCE` is the SAME flag ADR 0003 governs, so its re-homed enforce trigger also
applies: (a) the Option-D read path live in the env, (b) the mirror graph on the verdict path
(`type_release_gate` enabled — ADR 0004 redirected away from the abandoned Phase-T graph-expansion,
so `type_release_gate` is the remaining verdict-path condition, NOT Phase-T), (c) checksum-verify
(gap #1) in place.

The attestation **write/read seam** (how the projection attestation reaches the gate) is decided by
[ADR 0003](0003-cb-exact-convergence-attestation-seam.md): **Option D — RATIFIED (2026-08-07)**: CB
writes the immutable, release-keyed attestation into the CB-owned trials DB and EXACT reads it via
its read-only `trials` connection; **#307's HTTP endpoint is closed won't-do**. ADR 0003's monotonic
invariant ("activation permitted only when a durable, immutable attestation binds R to the projection
checksum the gate is activating") is exactly what gap #1's checksum verification enforces.

**v1 sequencing (all flag-off):** (1) **carry `therapy_release_id` through EXACT's patient input
contract first** — register it on the `PatientInfo` field registry + the promop/inline adapters so
`_build_in_memory` (which filters input against `PatientInfo._meta.get_fields()`) does not drop it,
and add a `get_user_therapy_release_id()` getter; **without this every patient reads null and
fail-closes even when the value was supplied.** Then the canonical patient-release parse + all-seams
decision behind an observe-only enforce toggle (mirrors `_PROJECTION_GATE_ENFORCE`); (2) the attestation seam per
ADR 0003; (3) activation-time recompute/verify vs the local projection, log-only; (4) projection
immutability / invalidate-on-mutation; (5) after Phase-T (#263) + the chosen attestation seam
live: enforce activation verification, then enable `patient == active-release`.

### Placement

The mirror tables and all mutable sync state live in a **new `default`-DB-owned Django app**
(not the `trials` app): the DB router sends `trials` models to the optional, externally
managed `trials` DB, where **migrations are disallowed by default** (only an explicit
`TRIALS_DB_MIGRATE` override enables them). A mirror model in the `trials` app therefore
could not have its table created there — so it could not be loaded. Keep it on `default`.

## Consequences

**Positive:** no reader ever sees an incomplete, mixed, or stale generation; the OMOP graph
expansion is reproducible against a named release (full cross-artifact reproducibility once
the release-match gate / versioned-projection contract holds — §Deferred); the graph is
available offline / independent of promop request-time availability; the active release is
observable end to end.

**Negative / cost:** local storage for the mirror (~18M `concept_relationship` rows) and its
indexes; a sync Cloud Run Job + scheduler + advisory-lock + verification/alerting; retiring
the #234 API+cache surface (`ConceptGraphClient` / `CachedConceptGraphClient` / the
`concept_graph` DB cache from slice-2b). The full cross-artifact/CB contract is real work,
deliberately deferred behind the release-match gate to avoid a cross-team stall.

## Invariant

> A reader never sees an incomplete, mixed-release, or stale-past-`MAX_MIRROR_AGE`
> generation. On any doubt — no verified release, a cross-artifact release mismatch, or an
> exceeded age bound — **fail closed** (503), never a silent wrong match.

This is the acceptance test for every change made under this ADR.
