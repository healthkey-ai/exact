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
| 4 | Cross-artifact drift (mirror R vs crosswalk/lookup/projection R-1) | **Release-match gate** at activation. The `default`-DB artifacts are checked in-transaction: the mirror generation is populated, and the `TherapyOmopMapping` crosswalk + `ComponentCategoryOmopLookup` are stamped for R + covered (the component-lookup check ships in #262; the crosswalk follows the same stamp+coverage pattern). The trial **`omop_*` projection lives on the CB-owned trials DB** — a live cross-DB read at activation is inherently racy, so it is gated instead via a **local attestation**: CB stamps every trial with its projection release, validates the whole batch, and **publishes a versioned "projection ready for R" attestation into EXACT's `default` DB**; the activation gate reads only that local attestation, so the flip stays atomic on one DB. Mismatch → do not activate, keep the last consistent bundle, alert. Provenance + attestation are a CB contract (CB `docs/exact-downstream-and-omop.md`; CB epic cancerbot-org/cancerbot#4653); the EXACT attestation model + gate are #265, **observe-only until Phase-T (#263)** puts the graph on the eligibility path. Coverage (projection concept_ids ⊆ R) is defense-in-depth, **not** provenance. |
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
