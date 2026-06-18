# ADR 0001: Cross-vocabulary mapping between EXACT, CTOMOP, and CancerBot

- **Status:** Proposed
- **Date:** 2026-06-18
- **Deciders:** EXACT team (pending review by CancerBot + CTOMOP terminology owners)
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

## Decision

Adopt a **hybrid** architecture:

1. **Mechanism — a single generic, versioned, reviewed crosswalk (was "option B").** One first-class mapping store keyed by `(source_system, source_vocabulary, source_concept_id | normalized_source_value)` to `(target_vocabulary = CB, target_code)`, covering all domains through one resolver, rather than 15 per-domain tables or 15 inline special-cases.

2. **Governance / source of truth — an externally owned, git-versioned artifact (was "option F").** The mapping is owned jointly by CB and CTOMOP terminology governance, not by any single application's internal loader. Ship a versioned artifact (e.g. JSON) compiled into an EXACT table; **do not** stand up a live network terminology service until multiple consumers actually require live resolution (it adds availability and reproducibility failure modes).

3. **Upstream emission — optional optimization only (was "option E").** CTOMOP may consume the same released artifact to emit pre-mapped values for stable production interfaces, but CTOMOP is **not** the source of truth (it must not silently encode CB semantics and EXACT-specific policy).

4. **Direction — map patient -> CB/EXACT vocabulary.** Trial criteria are authored and validated in CB semantics; one patient record is translated once, versus re-authoring every trial criterion. EXACT's therapy hierarchy and bespoke categories are not faithfully represented by single standard concepts. Preserve the OMOP identifiers and raw source values **beside** the resolved CB code; never destroy the source representation.

### Rejected options

- **Native standard vocabularies in EXACT (re-key taxonomies + trial criteria onto OMOP).** Rejected: many trial criteria have no lossless single OMOP concept; re-curation would cause semantic collapse, not eliminate mapping.
- **Denormalize CTOMOP values onto trials + dual-vocabulary matching.** Rejected as worst option: duplicated state, synchronization failures, ambiguous precedence, and stale concept_ids persisted on every trial. (See #172 for why drug-set identity is also unsafe: `vrd` and `vrd_lite` share an identical drug set but are distinct.)
- **Pure per-domain mapping tables (the literal "option A").** Rejected as primary: 15 schemas and workflows with inconsistent semantics. The generic crosswalk should carry domain as metadata instead.

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
relationship                # exact | narrower | broader | derived | lossy | no-map
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

3. **Per-attribute match metadata.** `therapy_related_things_match_status()` already returns `{ status: matched | not_matched | unknown, values: [...] }` per attribute, computed live in the detail path (`trial_details/trial_templates.py`). This stays in EXACT code-space; the only addition is an optional `matchSource` (`concept_id` | `source_value` | `name`) provenance field, which doubles as the observability signal for the shadow-mode rollout below.

## Rollout safety

- **Shadow mode first.** When both a concept_id and a name are present for a patient fact, resolve both, record disagreements as metrics, and **do not** change eligibility until the disagreement rate is validated near zero. On disagreement, treat as unknown / quarantine; never silently prefer one source.
- **Distinguish "absent" from "terminology layer failed."** These must be different states end to end.
- **Migration metrics to expose:** concept-id coverage, source-value coverage, name-only coverage, disagreement count, ambiguous-mapping count, eligibility-result diffs.

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
