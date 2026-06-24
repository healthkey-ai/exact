# ADR 0001: Cross-vocabulary mapping between EXACT, CTOMOP, and CancerBot

- **Status:** Proposed
- **Date:** 2026-06-18
- **Deciders:** EXACT team (pending review by CancerBot + CTOMOP terminology owners)
- **Reviews:** Codex (consult) on architecture options + the ETL re-keying idea; gstack `/plan-eng-review` (eng-manager pass) — substrate decision (OMOP `concept_relationship` upstream + compiled EXACT table), governance-as-Phase-0, per-domain kill switch, resolver test matrix, and outbound-annotation caching folded in.
- **Related:** #172 (HemOnc therapy concept_ids — first domain instance), CTOMOP PR #168 / issue #165

## Context

Three systems share clinical-trial matching but speak different vocabularies:

- **CancerBot (CB)** owns the clinical-trial data and its own vocabularies (therapy codes, markers, diseases, staging, etc.).
- **EXACT** (this repo) is the trial-matching engine. It imports CB trials and uses CB-derived vocabularies. Its taxonomies (`Therapy`, `Marker`, `Disease`, `TherapyComponent`/`TherapyComponentCategory`, `PlannedTherapy`, ...) are keyed by internal string `code`/`title`. It has essentially **no standard-vocabulary anchor on the matching path**: the only standard-coded fields are two free-text columns on the `Trial` model itself, `condition_code_icd_10` and `condition_code_snomed_ct` (`trials/models.py:342-343`), which describe a trial's target condition and are not consumed by the matcher or by any patient representation.
- **CTOMOP** is a separate OMOP-CDM patient-data service with its own and standard vocabularies (OMOP standard `concept_id`s: HemOnc, SNOMED, LOINC, RxNorm, plus source values).

**The task:** EXACT must search and match trials against patient values that arrive encoded in CTOMOP vocabularies, across **many domains** (not just therapy).

### Current state

The bridge lives in one file: `trials/services/patient_info/ctomop_adapter.py::normalize_ctomop_row`. It is a large hand-maintained crosswalk that resolves CTOMOP display strings and concept names to EXACT codes by **lowercased name-string lookup** against EXACT taxonomies, plus ~15 domain-specific special-cases: receptor status, TNM staging, histologic type, ethnicity, tumor/biopsy grade, therapy-line outcomes, refractory status, genetic mutations, lab column renames, therapies, behaviour flags, metastatic status, prior therapy, gender.

Problems with the current approach:
- **Brittle:** name-spelling drift between CTOMOP and EXACT silently drops values.
- **No provenance / no versioning:** mappings are executable code, not reviewable data.
- **Silent failure:** an unmapped value is indistinguishable from an absent value; both flow to the matcher as "unknown".
- **Clinical policy hidden in code:** transforms like `Equivocal -> HER2 low`, `Hispanic -> other`, grade-3 collapse, and regimen/component substitution are clinical decisions, not terminology synonyms, yet they are buried in aliases and inline transforms with no review trail.

CTOMOP has now started providing stable standard `concept_id`s (HemOnc Regimen ids for therapy lines, CTOMOP PR #168), which is the first stable cross-vocabulary anchor and the trigger for this ADR.

## Options considered (the four ways)

Where should the therapy (and every other domain's) crosswalk live? Four shapes, with good/bad for each.

### Option 1 — Expand the trials database
Denormalize CTOMOP-vocab columns (e.g. HemOnc `concept_id`s) onto EXACT's persisted trials and match on them.
- **Good:** single-vocab, fast SQL filtering; no runtime mapping; self-contained inside EXACT.
- **Bad:** two representations of the same requirement → drift; stale concept_ids persisted on every trial row; a destructive overwrite loses the CB criterion and is not re-derivable after a vocab update; the overlap logic gets duplicated across the SQL prefilter, the Python matcher, and the per-attr metadata builder; ambiguous source identity (`vrd` vs `vrd_lite` share a drug set); helps no other consumer (e.g. SoC). **Rejected** as the worst option (see Rejected options).

### Option 2 — EXACT does all the mapping of therapies → HemOnc concept IDs (in EXACT)
EXACT owns the crosswalk internally: today's `normalize_ctomop_row`, or a new EXACT-only mapping table.
- **Good:** no upstream dependency; ships today; EXACT fully controls its vocabulary; incremental.
- **Bad:** every consumer re-implements the same clinical mapping and they **diverge** — EXACT targets internal codes, SoC targets RxNorm, both hand-bridging the same CTOMOP fields; clinical policy (`Equivocal → HER2 low`, regimen→component) is duplicated and can silently disagree app-to-app; no shared provenance / versioning / review; maintenance × N consumers. `#174` is this option's bug surface. **Rejected as the primary mechanism** — it is the brittle status quo.

### Option 3 — CTOMOP exposes a crosswalk (chosen, with one refinement)
The source side, where the patient vocabulary and OMOP `concept_relationship` already live, owns and publishes the mapping; consumers read a version-pinned artifact.
- **Good:** one source of truth; OMOP `concept_relationship` is native to CTOMOP; one mapping serves EXACT and SoC; provenance / versioning / clinical review in one place.
- **Bad:** cross-team governance dependency (Conway — the Phase-0 risk); CTOMOP must **not** encode consumer-specific policy (EXACT's category buckets, SoC's RxNorm targets); a **live network service** would add availability / reproducibility coupling.
- **Refinements that make it the decision:** ship a **versioned compiled artifact, not a live service**; CTOMOP exposes only **source → standard anchor**, and **per-consumer target resolution stays consumer-side**.

### Option 4 — Dedicated ETL service that rewrites the whole trial corpus into CTOMOP vocabulary
A standalone ETL reads every CB trial and rewrites **all** its fields into CTOMOP vocab, so EXACT's persisted trials end up fully CTOMOP-vocab. Claim: then EXACT needs no crosswalk at all — both patient and trial sides speak CTOMOP-vocab, and all mapping lives at the ETL-service level. (Distinct from Option 1: Option 1 *adds* concept_id columns beside the CB fields; Option 4 *replaces* the trial corpus.)
- **Good:** centralizes the mapping in one service — but that half is already Option 3; EXACT runtime does no live mapping.
- **Bad:** the ETL **is** the crosswalk, relocated and run destructively — "no crosswalk needed" is false (the full CB-code → CTOMOP-concept mapping with relationship/lossy/no-map semantics is still required). **Blast radius** jumps from one stateless patient record per request to the **entire persistent trial corpus** — a bad mapping release silently shifts eligibility across all trials. Destructive overwrite **loses the CB source criterion** and is not re-derivable after a mapping fix or vocab update without re-running from CB. Coverage asymmetry (concept_ids only for MM therapy today) forces a **mixed-vocab corpus for years** → the matcher must run in two vocab spaces (the dual-vocab anti-pattern). Re-keying **breaks the therapy → component → category expansion** (`user_to_trial_attr_matcher.py`) and cannot express no-map / "A or B" composites (`cyclophosphamide_or_melphalan`) unless the entire EXACT taxonomy is ported into OMOP-space — which is the rejected native-OMOP option, done destructively. It maps the **expensive** persistent side to save runtime on the **cheap** stateless side. **Rejected** (Codex + eng review). It only wins as a **platform migration** (CB stops being the trial-authoring vocabulary AND CTOMOP has lossless reviewed coverage of every domain AND trials are curated natively in OMOP) — none true today. A *safe* ETL that preserves raw CB beside the projection is no longer "fully CTOMOP, no crosswalk"; it collapses back into Option 3.

## Decision

Adopt option 3 (refined) as the **hybrid** architecture:

1. **Mechanism — a single generic, versioned, reviewed crosswalk (was "option B").** One first-class mapping store keyed by `(source_system, source_vocabulary, source_concept_id | normalized_source_value)` to `(target_vocabulary = CB, target_code)`, covering all domains through one resolver, rather than 15 per-domain tables or 15 inline special-cases.

   **Substrate (eng review):** author and govern the mappings using **OMOP `concept_relationship` semantics on the CTOMOP/OMOP side** (`Maps to` / `Maps to value` / `Is a` — the standard vocabulary-relationship model; CTOMOP already added the vocabulary-relationship tables in `0065_add_vocabulary_relationship_tables`), then **compile a denormalized lookup table into EXACT** for runtime. This reuses a domain standard instead of re-inventing relationship semantics, and keeps governance where the OMOP vocabulary already lives. EXACT does not query OMOP vocab tables at match time — it consumes the compiled, version-pinned artifact (see Governance).

2. **Governance / source of truth — an externally owned, git-versioned artifact (was "option F").** The mapping is owned jointly by CB and CTOMOP terminology governance, not by any single application's internal loader. Ship a versioned artifact (e.g. JSON) compiled into an EXACT table; **do not** stand up a live network terminology service until multiple consumers actually require live resolution (it adds availability and reproducibility failure modes). **This is the critical path, not a side question (eng review / Conway):** the crosswalk spans three teams (CB owns trial vocab, CTOMOP owns patient vocab, a clinical reviewer owns equivalence). Naming the artifact owner and release process is **Phase 0** — every other phase depends on it, and an unowned crosswalk is the most likely way this stalls.

3. **Upstream emission — optional optimization only (was "option E").** CTOMOP may consume the same released artifact to emit pre-mapped values for stable production interfaces, but CTOMOP is **not** the source of truth (it must not silently encode CB semantics and EXACT-specific policy).

4. **Direction — map patient -> CB/EXACT vocabulary.** Trial criteria are authored and validated in CB semantics; one patient record is translated once, versus re-authoring every trial criterion. EXACT's therapy hierarchy and bespoke categories are not faithfully represented by single standard concepts. Preserve the OMOP identifiers and raw source values **beside** the resolved CB code; never destroy the source representation.

### Rejected options

- **Native standard vocabularies in EXACT (re-key taxonomies + trial criteria onto OMOP).** Rejected: many trial criteria have no lossless single OMOP concept; re-curation would cause semantic collapse, not eliminate mapping.
- **Denormalize CTOMOP values onto trials + dual-vocabulary matching.** Rejected as worst option: duplicated state, synchronization failures, ambiguous precedence, and stale concept_ids persisted on every trial. (See #172 for why drug-set identity is also unsafe: `vrd` and `vrd_lite` share an identical drug set but are distinct.)
- **Pure per-domain mapping tables (the literal "option A").** Rejected as primary: 15 schemas and workflows with inconsistent semantics. The generic crosswalk should carry domain as metadata instead.
- **ETL batch-rekeying of EXACT's persisted trials into CTOMOP vocabulary.** Rejected: it does not remove the crosswalk, it relocates it to ingest-time and makes it a **destructive** overwrite (loses the CB source criterion; not re-derivable after a vocab update without re-running from CB). It is also the harder direction (trial -> patient vocab) and loses EXACT's richer trial semantics — the therapy -> component -> category hierarchy the matcher depends on (`user_to_trial_attr_matcher.py:339`), "A or B" composites (e.g. `cyclophosphamide_or_melphalan`, `therapies_mapper.py:293`), and no-map criteria with no single CTOMOP concept. With concept_ids only for MM therapy today, it would force a mixed-vocab trials DB for years (the dual-vocab anti-pattern). Batch resolution is fine, but only as the compiled cache / upstream emission above, not as destructive re-keying. (Reviewed with Codex + eng review.)

## Required shape of the crosswalk

Do not key it by Django class name (`exact_model`) — that is an implementation detail. Minimum mapping identity:

```
source_system
source_vocabulary
source_vocabulary_version
source_domain
source_concept_id | normalized_source_value
target_vocabulary           # CB
target_vocabulary_version
target_domain
target_code
relationship                # align to OMOP relationship vocabulary: Maps to (exact) | Maps to value | Is a (narrower) | Subsumes (broader) | derived | lossy | no-map
mapping_status              # candidate | reviewed | unmapped
valid_from / valid_to
provenance
clinical_context
reviewer
reviewed_at
```

**`relationship` is mandatory and is the core insight:** a crosswalk is not a synonym table. Many mappings are contextual, directional, one-to-many, or impossible. Equivalence, narrowing, broadening, and lossy collapses must be distinguishable so that downstream code can refuse to let a lossy or unmapped fact strengthen eligibility.

Do **not** put a global unique constraint on `(vocabulary, concept_id)` unless one-to-one semantics are proven.

## Three integration points (bidirectional)

The crosswalk is **bidirectional** and serves three call sites, not one. The first version of the analysis covered only inbound matching; this ADR corrects that.

1. **Inbound resolution (matching).** `ctomop_adapter` resolves CTOMOP `concept_id` / source value to EXACT `code`(s); the existing matcher then runs unchanged in EXACT code-space (`user_to_trial_attr_matcher.py`), preserving therapy -> component -> category expansion. Resolution order:
   1. reviewed concept-id mapping,
   2. reviewed source-value mapping,
   3. explicitly recorded `unmapped` result.
   Never automatic title/name matching in production.

2. **Outbound option annotation (presentation).** The trial detail response (`trials/services/trial_details/trial_attributes.py`, via the trials retrieve endpoint) and `form-settings` (`FormSettingsViewSet`) return the full option set for select / multi-select attributes (therapies, markers, receptors, mutations, staging, ...). Today these come purely from EXACT vocabularies via `ValueOptions.all_options()` (`{value: code, label: title}`) with no mapping out. For CTOMOP consumers, each option must be **annotated** from the crosswalk:
   ```json
   { "value": "vrd", "label": "VRd (...)", "conceptId": 35803361,
     "mappingStatus": "reviewed", "relationship": "exact" }
   ```
   Options whose relationship to CTOMOP is `no-map` / `broader` / `lossy` must be flagged so a consumer does not present a selectable option that can never (or wrongly) light up from CTOMOP patient data. Conversely, CTOMOP patient values with **no** EXACT option need an explicit `unmapped` / `other` representation so the patient's real value is not silently invisible in the detail view.

   **Performance (eng review):** `ValueOptions.all_options()` is already cached (`cache_key` v2). Fold the crosswalk annotation **into that cached blob**, not a per-request join — otherwise every trial-detail / form-settings response does an N-options crosswalk lookup. Bust the cache on crosswalk-artifact version change.

3. **Per-attribute match metadata.** `therapy_related_things_match_status()` already returns `{ status: matched | not_matched | unknown, values: [...] }` per attribute, computed live in the detail path (`trial_details/trial_templates.py`). This stays in EXACT code-space; the only addition is an optional `matchSource` (`concept_id` | `source_value` | `name`) provenance field, which doubles as the observability signal for the shadow-mode rollout below.

## Rollout safety

- **Shadow mode first.** When both a concept_id and a name are present for a patient fact, resolve both, record disagreements as metrics, and **do not** change eligibility until the disagreement rate is validated near zero. On disagreement, treat as unknown / quarantine; never silently prefer one source.
- **Distinguish "absent" from "terminology layer failed."** These must be different states end to end.
- **Migration metrics to expose:** concept-id coverage, source-value coverage, name-only coverage, disagreement count, ambiguous-mapping count, eligibility-result diffs.
- **Per-domain kill switch (eng review):** the concept-id path must be enableable/disableable **per domain**, so a bad mapping release for one domain rolls back to the name path for that domain alone, not the whole crosswalk. Blast radius of a bad release = one domain.

### Resolver test matrix (eng review)

The resolver is the highest-risk new code (the current bridge's silent-failure bugs are tracked in #174). The invariant is only a guarantee if it is tested. Required matrix, as its own module (not inline in `normalize_ctomop_row`), with the crosswalk as data:
- each relationship type (`Maps to` / `Maps to value` / narrower / broader / lossy / no-map) resolves to the correct status, and lossy/no-map never clears an exclusion;
- ambiguous mapping (e.g. `vrd` vs `vrd_lite`) fails closed, not by dict/import order;
- `unmapped` (terminology-layer failure) is distinct from `absent` (no patient value) end to end;
- id-vs-name disagreement in shadow mode is recorded, never silently resolved.

## Invariant

> No unresolved or lossy fact may silently strengthen eligibility.

This is the acceptance test for every change made under this ADR.

## Consequences

**Positive:** one reviewable, versioned, auditable mapping mechanism replacing brittle inline string lookups; clinical policy becomes data with provenance and review; graceful degradation during the long partial-concept-coverage period; the same artifact serves matching, option annotation, and (optionally) CTOMOP's own emission.

**Negative / cost:** requires a governance process and owners across two (three) teams; requires building the crosswalk store, loader, CI validation, and shadow-mode instrumentation; back-filling reviewed mappings for ~15 domains is real curation work, done incrementally (therapy / MM first, per #172).

## Known correctness bugs in the current bridge

These were surfaced during analysis and verified against the code. They are clinical-safety issues (false eligibility from information loss), tracked separately from this ADR:

- `ctomop_adapter.py` `resolve_code_csv` silently drops unresolved items from multi-value fields (markers, medications). An unmapped **excluded** fact can disappear and produce a false `eligible`. (verified)
- `trials/querysets/trial.py` `eligible_for_required_and_excluded_lists` returns `self` (no filtering) when the patient value list is empty / fully unmapped, so all candidates pass the prefilter. (verified)
- Unmapped scalar values become `None`, which the aggregate `trial_match_status` reports as `potential` rather than a mapping failure. (consistent with code)
- `_match_type_bool_restriction` (`user_to_trial_attr_matcher.py:731`) converts a missing value to `False`; when the trial value is `True` and the attribute is not `under_user_control`, it returns `not_matched` (an actual exclusion) rather than `unknown` (uncertainty). (verified)
- Process-lifetime cached title mappings (`_build_code_lookup`) can go stale after a taxonomy update. (flagged)
