# ADR 0003: CB↔EXACT convergence — the vocab-mirror / projection-attestation seam

- **Status:** Accepted — **Option D ratified** (2026-08-07). Supersedes #307 (HTTP publish channel) and rejects B/E; retires the C interim posture. Enforce-flip trigger re-homed off Phase-T per [ADR 0004](0004-patient-expansion-tell-dont-ask.md) (see Decision).
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

### D. (Chosen — ratified) Attestation on the trials DB — no publish channel
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

## Decision

**Option D is chosen (ratified 2026-08-07): CB writes the immutable, release-keyed
projection attestation into the CB-owned trials DB; EXACT reads it read-only via the
existing `trials` alias at activation.** No publish channel: **#307 (HTTP) and B/E are
closed won't-do**, and the **C interim posture is retired** — D is the committed
topology.

Basis for ratification:
- **The read path is proven (spike, 2026-08-07).** In the local HT stack a
  `ProjectionAttestation` table was created on the `trials` DB (`exact_trials`) with a
  CB-side write for the active release; EXACT read it back through the `trials` alias
  with the **same model and zero `default`-DB schema** — attested → `True`, unattested
  → `False` (fail-closed). Confirmed a genuine cross-DB read (`trials`=`exact_trials`
  vs `default`=`exact`; no row on `default`), and that raw DDL on the trials DB works
  under the router's `allow_migrate=False` (CB owns the table, EXACT only reads).
- **The remaining preconditions are policy, not code, and are hereby accepted as
  commitments** (item list below): (i) releases immutable, `release_id` never reused;
  (ii) attestations insert-only (no delete/update — enforced at DB grants); (iii) the
  row binds R to the projection checksum the gate activates. A lagging read replica is
  acceptable — being monotonic, D can only *miss* an attestation (false-negative,
  fail-closed), never fabricate one; mount strong-consistency is **not** required.

Implementation delta from today (a follow-up, not a re-decision):
- The `ProjectionAttestation` model + gate (#265 / PR #306) live on EXACT's **`default`**
  DB today, written by the `publish_projection_attestation` management command. Under D
  the table **relocates to the CB-owned `trials` DB**, written by CB in the same unit as
  sealing the projection; EXACT keeps a **read-only** model routed to `trials`.
- **Routing is not automatic** (relocation-issue detail): `TrialsDatabaseRouter` keys on
  `app_label == 'trials'`, but `ProjectionAttestation` is a **`vocab_mirror`** model, so
  ordinary ORM reads still hit `default`. The relocation must do one of: move the
  read model into the `trials` app, add an explicit router exception for it, or read via
  `.using('trials')`. It must be read-only from EXACT (`allow_migrate=False` — CB owns
  the DDL). Miss this and the moved table reads as missing → the gate fail-closes every
  activation.

Enforce-flip trigger (corrected for [ADR 0004](0004-patient-expansion-tell-dont-ask.md)):
- #265's enforce flip (`_PROJECTION_GATE_ENFORCE`) was gated on BOTH this topology
  decision **and Phase-T (#263)**. **Phase-T is redirected by ADR 0004** (EXACT no
  longer expands regimens on the eligibility path), so the Phase-T half of the trigger
  is void. Re-home it: flip enforce only when **(a)** the D read path is live in the
  target environment; **(b)** the mirror is genuinely on the verdict path there — i.e.
  while the `type_release_gate` (#286) is enabled (ADR 0004's transitional posture); and
  **(c)** the gate actually **verifies the projection checksum**, not just row existence.
  (c) is load-bearing: `projection_attested()` is an existence-only check today and
  `checksum` is an optional/blank-default column, so enforcing on existence alone would
  activate on a blank or mismatched attestation. Before the flip, the gate MUST bind +
  verify `release_id` → `checksum` (and reject blank/mismatch), and attestations MUST be
  insert-only + immutable (per Decision content). If/when `type_release_gate` retires
  (ADR 0004 end-state) with no mirror-backed verdict dependency left, the projection-gate
  enforce obligation falls away with it. Never flip enforce without a working, checksum-
  verifying read path (else every activation fail-closes permanently, or worse, fail-opens
  on a blank row).

## Decision content (D, ratified)

The commitments the chosen option must pin, resolved for D. Where the existing
`ProjectionAttestation` model (#265 / PR #306) already answers, that is noted; open
items are impl detail for the relocation issue, not re-decisions.

1. **Attestation identity/schema:** `release_id` (unique), projection `checksum`,
   `run_id`, `trial_count`, timestamp — the existing model's fields. The checksum
   **algorithm + scope** and any `schema/vocab version` + `producer version` columns
   are finalized in the relocation issue (add columns if the CB seal needs them);
   `release_id` + `checksum` are the load-bearing pair the gate binds.
2. **Lifecycle:** CB writes the attestation **in the same unit as sealing the
   projection** for release R (after its release-wide backfill verifies every trial is
   stamped for R). Delete/update prohibited — enforced at DB-permission level on the
   CB-owned `trials` DB.
3. **Conflict semantics:** unique `release_id` + insert-only. Idempotent re-publish of
   the *same* attestation is fine; a second *different* attestation for R is an
   **incident**, never an overwrite (the DB unique constraint makes the overwrite
   impossible; the incident is the surfaced signal).
4. **Read semantics (D):** EXACT reads through the `trials` alias at activation. A
   **lagging replica is tolerated** (monotonic → fail-closed false-negatives only);
   **mount strong-consistency is NOT required**. The gate reads the single row for the
   activating `release_id`; absent/mismatched → do not activate (fail closed).
5. **Trust boundary:** writes restricted to CB's **release-finalization workflow**, not
   arbitrary CB app credentials — a DB grant on the `trials` DB scoped to that principal.
6. **Failure policy:** fail closed + alert on missing/mismatched attestation; keep the
   last consistent bundle; runbook for reconciliation. (Enforce stays observe-only until
   its trigger — see Decision.)
7. **Revocation/rollback:** an attested release is disabled via **mutable revocation
   state kept separate** from the immutable attestation, so revocation never mutates the
   insert-only attestation row. **Location resolved by [ADR 0002](0002-vocab-mirror-consumer-consistency.md)
   §"Gate 1 gap #2 resolution": the `disable(R)` record MUST be co-located with the projection on the
   CB-owned `trials` DB and read with it in one `REPEATABLE READ` snapshot** — an EXACT-side /
   `default`-DB revocation list (floated as an example in earlier drafts) is **fail-open** (it cannot
   share the projection's snapshot) and is superseded.

## Consequences

- **Positive:** no speculative HTTP surface (#307 avoided); D is the cheapest,
  most-correct shape (co-located write, no new channel); the decision + invariant
  are captured, not implicit in #307.
- **Remaining risk (now scoped, not open-ended):** the topology is decided, so the
  parked work is concrete — **relocate the attestation to the `trials` DB** (the
  follow-up issue) and, separately, **flip enforce** once its (re-homed) trigger holds.
  The old "make the choice before the first production cutover" hard-trigger is
  **discharged** (the choice is made). Phase-T is no longer a trigger input (ADR 0004).
- **Follow-ups:** align with promop `healthkey-core` (#46/#54) on package
  ownership of the vocab/attestation seam; update CB
  `docs/exact-downstream-and-omop.md` with the chosen seam.

## Open questions

1. ~~**D spike** — prove D end-to-end in the local HT stack.~~ **RESOLVED
   (2026-08-07):** the cross-DB read path is proven (see the spike note under
   Decision). What remains for D is a policy guarantee (release-immutability +
   insert-only), not a technical unknown.
2. ~~Does CB embedding `exact_matching` drag in an EXACT-`default`-DB connection…~~
   **Moot for this decision:** D uses no embed and no `default`-DB write, so the
   matcher-seam / vocab-seam independence no longer gates the topology choice. (Still a
   real question for the `exact_matching` convergence work — tracked there, not here.)
3. **Governance — decided:** ratified here as **EXACT's consumer-side decision**; the
   attestation contract it depends on (CB writes the `trials`-DB row) is a **CB
   commitment** to coordinate via CB `docs/exact-downstream-and-omop.md` + promop
   `healthkey-core` (#46/#54, #254). A cross-repo/platform re-ratification is optional
   follow-up, not a blocker — EXACT's read-side gate is fully specified by this ADR.
