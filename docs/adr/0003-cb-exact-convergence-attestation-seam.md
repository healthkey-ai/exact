# ADR 0003: CB↔EXACT convergence — the vocab-mirror / projection-attestation seam

- **Status:** Proposed
- **Date:** 2026-08-07
- **Deciders:** cross-repo / platform decision (EXACT + CB owners; the shared-lib
  direction is a HealthKey-platform call, Adam Blum). ADR file lives in EXACT but
  the decision binds CB + promop.
- **Reviews:** Codex (design pass) + gstack `/plan-eng-review` (eng-manager pass) —
  both folded in. Codex added Option D + reframed ADR-0002's "cross-DB read is
  racy" to a monotonic-fact false-negative-only argument; plan-eng-review verified
  D against the split-DB code, narrowed its precondition (a lagging replica is
  acceptable → the hard reqs are release-immutability + insert-only attestations),
  surfaced the table-ownership (Conway) consequence, and asked for a spike before
  locking.
- **Relates:** builds on [ADR 0002](0002-vocab-mirror-consumer-consistency.md)
  (mirror consumer + attestation gate) and the CB convergence design
  (CB `docs/exact-downstream-and-omop.md`; the QR1–QR5 plan; `exact_matching`
  package). Coordinates with promop **healthkey-core** extraction (promop #46,
  #54, #97) and promop ADR 0001. Decides the fate of **#307** (HTTP attestation
  endpoint) and constrains the #265 projection-gate enforce flip.

## Context

Two workstreams converge on the same seam from opposite premises.

1. **ADR 0002 (vocab-mirror consumer)** treats CB and EXACT as **separate
   deployed services with separate DBs.** The trial `omop_*` projection lives on
   the CB-owned trials DB; the vocab mirror + activation gate live on EXACT's
   `default` DB. ADR 0002 has the gate read a **local attestation** in `default`
   rather than a cross-DB read of the trials table, and — because "CB is a remote
   service that cannot run EXACT's `manage.py`" — spawned **#307: an HTTP endpoint**
   as CB's write channel.

2. **The CB convergence (design doc + QR1–QR5 plan)** moves the opposite way:
   extract CB's trials **search + matcher** into a shared, pip-installable Django
   package (`exact_matching`) that **CB embeds in-process**, converging *per axis*.
   QR1–QR3 landed. In parallel promop extracts a reusable **`healthkey-core`**
   package (#46) with a **plugin** base class (#54), and CB patients move to
   promop **PatientRecord**. End-state: **CB hosts EXACT/PROMOP capabilities as
   embedded packages/plugins**, not over HTTP.

**The collision.** #307's HTTP transport is a direct consequence of (1)'s
separate-services topology. If (2) reaches the vocabulary seam, publishing an
attestation becomes a **function call** and **#307 is dead on arrival.** Today the
convergence has covered only the **matcher**; the **vocab_mirror/attestation seam
is in no plan or ADR** — its topology is genuinely undecided and unwritten.

## The real invariant (correcting an ADR-0002 overstatement)

A local `default`-DB attestation makes the **gate's own state transition**
transactional; it does **not** make the trial projection and the attestation
atomic across DBs (a local copied row can still be stale or published early).
Transport does not buy correctness. The property we actually need is:

> **Activation of release R is permitted only when a durable, immutable
> attestation binds R to the exact projection identity (checksum/version) the gate
> is activating.**

Given that, and if the attestation fact is **monotonic** (once
`attestation(release_id, checksum, …)` exists it never changes/disappears;
`release_id` is never reused; it is written only after the projection is sealed),
then any lag/unavailability on the read path can cause only a **false negative**
("cannot activate yet" — safe, fail-closed), never a **false positive**
(activating an unattested/mismatched release). This reframes the ADR-0002
"cross-DB read is racy" objection: for a monotonic, release-keyed row it is **not**
a correctness blocker — only a liveness/lag concern.

## Options

### A. Service boundary — build #307 (HTTP)
CB stays a separate service; publish via #307. **Pros:** no coupling to EXACT's
`default`-DB schema/creds; clean service-auth. **Cons:** bets against the
convergence; becomes legacy surface if CB later embeds the vocab seam.

### B. Embed the vocab seam — CB calls publish in-process
Move the attestation publish (and maybe the mirror) into the shared package; CB,
configured with EXACT's `default` DB, calls `publish_projection_attestation()`
in-process. **Pros:** matches the end-state; one code path. **Cons:** couples CB to
EXACT's `default`-DB connection; larger than the matcher convergence has taken on;
premature until the QR roadmap reaches the vocab/patient seam.

### D. (Likely best) Attestation on the trials DB — no publish channel
CB writes the immutable, release-keyed attestation **into the CB-owned trials DB**
(it already owns it, writes it in the same unit as sealing the projection); EXACT
reads it via its **existing read-only trials connection** (`trials` DB alias,
`TrialsDatabaseRouter`) during activation. Given the monotonic invariant above,
the cross-DB read yields only fail-closed false negatives. **Pros:** eliminates
the publish channel entirely (#307 and B both moot); write and projection are
co-located in CB's own DB/transaction (stronger than any copy); no new auth
surface; spends zero innovation tokens. **Precondition (narrower than it looks —
plan-eng-review refinement):** because the fact is monotonic, D tolerates a
**lagging read replica** — a stale replica can only *miss* an attestation
(false-negative, fail-closed), never fabricate one. So D's *hard* requirements are
just: (i) releases are immutable and `release_id` is never reused; (ii)
attestations are never deleted/updated (insert-only); (iii) the row binds R to the
projection checksum. Mount *strong-consistency* is **not** required. **Cons /
consequence:** the attestation table moves from EXACT's `default` DB (EXACT-owned,
today) to the **CB-owned trials DB** — CB now owns the schema+writes of a table
EXACT's gate reads (a deliberate Conway coupling: it aligns ownership with the
data, since CB already owns the projection). EXACT needs a read-only model routed
to `trials`.

### E. (For completeness) CB writes EXACT's `default` DB directly
Like B but without embedding: grant CB a connection to EXACT's `default` DB; CB
inserts the attestation row itself. Drops #307's HTTP surface but couples CB to
EXACT's `default`-DB schema+creds *and* points CB's write at a DB it doesn't own —
strictly worse than D (which writes CB's own DB). Listed only to be explicitly
rejected in favour of D.

### C. Interim posture — single seam, transport frozen
Keep `publish_projection_attestation(...)` as the **one seam** (mgmt-command
adapter exists today). **Do not build #307.** Hold until D is proven or refuted,
then commit to D (or A/B). Not a final answer — an execution posture.

## Decision (proposed)

**Prefer D, subject to proof; adopt C as the interim posture; A/B only if D fails.**

- **Freeze #307** (do not implement); relabel "blocked on this ADR".
- **Validate D's (narrow) preconditions** before committing — note a lagging
  replica is acceptable (fail-closed), so the checks are: (i) releases are
  immutable, `release_id` never reused; (ii) attestations are insert-only (no
  delete/update — enforce at DB grants); (iii) the row binds R to the projection
  checksum the gate activates. If all hold, **D wins** and #307 + B/E are closed
  won't-do. If any fails, choose **A or B explicitly** — before enforcement.
- **De-risk with a spike — DONE (2026-08-07, read path proven).** In the local HT
  stack a `ProjectionAttestation` table was created on the `trials` DB
  (`exact_trials`) with a CB-side write for the active release; EXACT read it back
  through the `trials` alias with the **same `ProjectionAttestation` model and zero
  `default`-DB schema** — attested release → `True`, unattested → `False`
  (fail-closed). Confirmed a genuine cross-DB read (`trials`=`exact_trials` vs
  `default`=`exact`; no row on `default`), and that raw DDL on the trials DB works
  under the router's `allow_migrate=False` (so CB owns the table, EXACT only reads).
  The read path is **not** the risk; the remaining D preconditions are policy —
  release-immutability + insert-only attestations — not code.
- The **#265 enforce flip stays gated on BOTH** this topology decision *and*
  Phase-T (#263); never flip enforce without a working publish/read path for the
  chosen option (else every activation fail-closes permanently).

## Any option MUST specify (decision content)

1. **Attestation identity/schema:** `release_id`, projection checksum (algorithm +
   scope), schema/vocab version, source/run id, producer version, timestamp.
2. **Lifecycle:** who seals a release and when the attestation is written; delete/
   update prohibited (enforce at DB-permission level).
3. **Conflict semantics:** unique key + immutable insert; a second *different*
   attestation for R is an **incident**, never an overwrite (idempotent re-publish
   of the *same* attestation is fine).
4. **Read semantics (D):** required mount consistency + tolerated lag behaviour.
5. **Trust boundary:** which principal may attest — restrict writes to the
   release-finalization workflow, not arbitrary CB app credentials.
6. **Failure policy:** fail closed + alert + retry/reconciliation + runbook.
7. **Revocation/rollback:** can an attested release be disabled, and where does
   that (mutable) revocation state live — distinct from the immutable attestation.

## Consequences

- **Positive:** no speculative HTTP surface; D (if it holds) is the cheapest,
  most-correct shape (co-located write, no new channel); the decision + invariant
  are captured, not implicit in #307.
- **Negative / risk:** the enforce path stays parked until D is proven or A/B
  chosen. **Hard trigger:** make the choice *before* the first production cutover
  that turns enforcement on (or Phase-T landing) — whichever comes first — so this
  never blocks a release under pressure.
- **Follow-ups:** align with promop `healthkey-core` (#46/#54) on package
  ownership of the vocab/attestation seam; update CB
  `docs/exact-downstream-and-omop.md` with the chosen seam.

## Open questions

1. ~~**D spike** — prove D end-to-end in the local HT stack.~~ **RESOLVED
   (2026-08-07):** the cross-DB read path is proven (see the spike note under
   Decision). What remains for D is a policy guarantee (release-immutability +
   insert-only), not a technical unknown.
2. Does CB embedding `exact_matching` drag in an EXACT-`default`-DB connection, or
   are the matcher seam and vocab seam independent (making B a separate later bite)?
3. Governance: this ADR lives in EXACT for now, but the decision binds CB + promop
   `healthkey-core` — confirm whether it should be ratified as a cross-repo/platform
   ADR (coordinates with promop #254).
