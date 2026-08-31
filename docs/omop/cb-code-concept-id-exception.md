# The `cb_code ↔ concept_id` exception

**Status:** Active temporary exception (documented per #236)
**Owner:** CancerBot terminology governance (CB owns the upstream mapping, CB #4476).
EXACT reads/populates it **locally** but does not own the curation.
**Governing decision:** [promop ADR 0001 — *promop is the vocabulary source of
truth*](https://github.com/healthkey-ai/promop/blob/dev/docs/adr/0001-vocabulary-source-of-truth.md),
which supersedes [EXACT ADR 0001](../adr/0001-cross-vocabulary-mapping.md).

## Why this exists

promop ADR 0001 makes promop the **single** vocabulary source of truth: consumers
pull promop-owned standard OMOP concepts (and the concept graph) via API + cache
rather than vendoring them. The permitted exception is **not** a promop vocabulary
copy — it is **consumer-specific policy** (CB drug-class *categories*) that promop
must not encode. This document records that one exception inside EXACT (ported from
CB), why it cannot be removed today, and the gate that retires it.

> **Scope note.** This is a policy statement about promop-owned vocabulary. EXACT
> still persists derived OMOP state — `OmopConcept` titles, the `omop_concept_id`
> columns on its vocab models, and the trial `omop_*` projections. Those are
> compiled/derived artifacts, not the exception; the exception is specifically the
> `cb_code → CB-category` policy below.

The exception is the **`cb_code ↔ concept_id` bridge**, which exists in two forms:

- **Curated record (build-time):** `trials.models.TherapyOmopMapping`.
  - **Exact key:** `(level, cb_code)` — where `level ∈ {regimen, component, category}`
    and `cb_code` is CancerBot's internal therapy code. It is **`cb_code`, not
    `cb_id`** — the key is the string code, not a surrogate id.
  - **Source of record:** the curated CSV
    `docs/omop/mapping/therapy_omop_mapping.csv`, materialized into the DB by
    `manage.py load_therapy_omop_concept_ids`.
  - **Rows kept for audit, not runtime:** `match ∈ {needs_review, no_omop}` carry no
    concept_id; `match ∈ {crosswalk_only, deferred}` carry a concept_id for **audit
    only** and are **not** runtime-applicable (CB #4580). Only `auto/curated/llm` are
    applied.
- **Runtime-applied form:** the `omop_concept_id` columns written onto the vocab
  models **and** the flat `component concept_id → CB category codes` reverse-map
  `vocab_mirror.models.ComponentCategoryOmopLookup` (moved from `trials` in #262 so
  it is writable on `default` and release-gated), read on the match path by
  `trials/services/omop/therapy_graph.py`. This runtime lookup — not the curated
  record — is what the retirement gate must observe as quiescent.
- **Version (policy intent, not yet enforced):** the concept_ids are curated against
  a specific promop/OMOP vocabulary release and are only valid against it, so a
  release bump **should** trigger re-curation. Note this is currently a **policy
  intent**: neither the CSV nor `TherapyOmopMapping` records a release id, and the
  concept-graph cache's release is caller-supplied and not verified against promop's
  response — so re-curation on bump is **not machine-enforced** today.

## Why it can't be retired by HemOnc coverage alone

The bridge is **not** merely "CB therapy code → HemOnc concept_id" (which promop's
API could supply directly). EXACT **reverse-maps** component `concept_id`s → CB
**category** codes at runtime (`trials/services/omop/therapy_graph.py`, backed by
`ComponentCategoryOmopLookup`). Those CB drug-class *categories* are an
EXACT/CB-specific policy that promop must **not** encode (per promop ADR 0001 §
"promop must not encode consumer-specific policy").

Therefore, even once promop has complete HemOnc concept coverage, the trial
**criteria** that match on CB categories still have to be **re-authored** in
concept-native terms before the bridge can be dropped. Coverage is necessary but
not sufficient.

## Retirement gate

The exception is retired — and this document closed — only when **all** of the
following hold (from #236):

1. **Replacement authored.** Every *active* CB trial criterion that uses the bridge
   has an approved concept-native replacement (the CB-category criteria are
   re-authored, not just remapped).
2. **Semantics preserved.** Direct matching against the promop **release cache**
   preserves what the bridge preserved: **exclusions**, **ambiguity** (e.g. `vrd`
   vs `vrd_lite` fail closed, not by ordering), and **source identity**. No
   lossy/unmapped fact may strengthen eligibility (the ADR 0001 Invariant).
3. **Quiescent for ≥ 2 releases.** Zero production use of the **runtime-applied
   form** — the `ComponentCategoryOmopLookup` reverse-map (and the `omop_concept_id`
   columns it derives from) on the match path — across **at least two** promop
   vocabulary releases (observed, not assumed). Watching only the curated
   `TherapyOmopMapping` record is insufficient: it is build-time, so it can be
   quiescent while the runtime reverse-map is still active.
4. **Archive, don't delete.** The final mapping is kept as a **versioned archive
   for audit only** (provenance of past eligibility decisions), never as a live
   runtime input.

Until every gate is met, the bridge stays — scoped to therapy, owned by CB
governance, pinned to a promop release, and documented here.

## Expiry

**No implicit expiry by date** — expiry is the retirement gate above. This
exception must be **re-reviewed at every promop vocabulary release bump** (the
pinned release is the natural checkpoint): confirm the gate is still unmet and the
pin/curation is refreshed, or begin retirement. An exception that stops being
re-reviewed at release bumps is out of policy.
