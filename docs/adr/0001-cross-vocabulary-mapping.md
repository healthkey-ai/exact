# ADR 0001: Cross-vocabulary therapy matching (CB ↔ OMOP ↔ EXACT)

- **Status:** Accepted. **Authoring (CB crosswalk) + materialized trial `omop_*` columns + the flag seam are implemented** behind `EXACT_OMOP_THERAPY` (default OFF) on branch `feat/omop-crosswalk-exact` (EXACT PRs #192–#208, #4476; CB epic #4447), **not yet merged to `main`**. The **matching design (§3–§4) is the decided target** — the feat branch currently uses an interim graph-based derivation; see "Delta from the current feat-branch implementation" + "Migration". Non-therapy domains: proposed (future, last section).
- **Date:** 2026-06-18 (rewritten 2026-06-19)
- **Deciders:** EXACT + CancerBot teams
- **Reviews:** Codex (consult) + gstack `/plan-eng-review` on the options; this revision reconciled against the as-built code after auditing CB epic #4447 and the EXACT port.
- **Related — implementation (already built):** CB epic **#4447** (and #4442/#4444/#4445/#4451/#4453/#4455/#4457/#4446); EXACT port **#4476**. Source-of-truth docs in CB: `docs/exact-downstream-and-omop.md`, `docs/omop/therapy-paths-audit.md`; in EXACT: `docs/porting-from-cancerbot.md`. Patient side: CTOMOP PR #168 (HemOnc concept_ids on PatientInfo). Trackers: #172, #173, #174.

> **Revision note.** The first version of this ADR was written greenfield and proposed a generic patient→EXACT boundary resolver. That framing was **wrong for therapy**: a coordinated CB→EXACT epic had already built a different, better-fitting design (CB authors the crosswalk and materializes OMOP `concept_id` columns on trials; EXACT matches on `concept_id` overlap behind a feature flag). This rewrite documents the **as-built** therapy design and the requirement that drives it, and scopes the generic-resolver vision to the not-yet-built non-therapy domains.

## Context

Three systems share clinical-trial matching but speak different vocabularies:

- **CancerBot (CB)** owns the clinical-trial data and its own vocabularies (therapy codes, markers, diseases, staging, …). **CB is upstream**; EXACT is a downstream port (~95% identical matcher).
- **EXACT** (this repo) is a **stateless, read-only** trial-matching engine. Its taxonomies (`Therapy`, `TherapyComponent`, `TherapyComponentCategory`, …) are keyed by internal `code`/`title`.
- **CTOMOP** is an OMOP-CDM patient-data service providing standard `concept_id`s (HemOnc regimens, RxNorm drugs, SNOMED, LOINC).

**The task:** match trials against patient data that arrives in CTOMOP/OMOP vocabularies, on a shared vocabulary rather than CB-private string codes — so a stateless downstream (EXACT) and external patient sources (CTOMOP) interoperate.

Two bridges exist, and they are different things:
- **Legacy generic bridge (name-string):** `ctomop_adapter.py::normalize_ctomop_row` resolves CTOMOP display strings to EXACT codes by lowercased name lookup across ~15 domains. Brittle, no provenance, silent failure (the bugs in #174). Still the path for every non-therapy domain.
- **OMOP therapy bridge (concept_id, this ADR):** the built design below, for regimen + drug-component therapy matching.

## Key requirement: the trials DB is a read-only-replica distribution surface

EXACT's trials DB is intended to be consumed as a **read-only replica by other projects in the future**, not only by EXACT's own matcher. A pure SQL replica consumer can only `SELECT`; it **cannot** run a Python resolver, load a mapping artifact, or execute an ETL. Therefore the OMOP concept_ids must be **materialized, self-describing columns on the trial rows** — the data has to carry its own vocabulary. This requirement is decisive: it rules *in* "concept_id columns on trials" and rules *out* any design that needs the consumer to resolve at read time.

## Options considered (the four ways)

The original framing pitted "columns on trials" against "resolve at a boundary" as rivals. That was a **false dichotomy** — there are two independent axes: **where the mapping is authored** vs **how it is distributed to consumers**. The read-only-replica requirement fixes the distribution axis to materialized columns.

### Option 1 — Concept_id columns on the trials DB *(distribution layer — adopted)*
Materialize OMOP `concept_id` columns on the persisted trials; consumers match/query on them.
- **Good:** self-describing for **any** read-only-replica consumer (the key requirement above); single-vocab, GIN-indexed SQL filtering; no runtime mapping for the consumer.
- **Bad (only in a *naive* form):** if there were two independent sources of truth they would drift. The as-built design removes this: **one** authority (CB) fills the columns from **one** vocab map, a single locked writer, and `shadow_compare` detects drift. So the columns are an additive projection of a single source, not a second source.
- **Verdict:** **adopted as the distribution layer.** (The original "rejected as worst" verdict assumed EXACT's matcher was the only consumer and conflated this with the destructive Option 4 — both wrong given the replica requirement.)

### Option 2 — Each consumer maps internally
Each app hand-bridges CTOMOP fields to its own vocabulary (today's `normalize_ctomop_row`; SoC's `_*_codes.py`).
- **Good:** no upstream dependency; ships today.
- **Bad:** N consumers re-implement the same clinical mapping and diverge; clinical policy duplicated; no shared provenance. `#174`/`#198` are its bug surface.
- **Verdict:** **rejected** as the therapy mechanism (it is the brittle status quo that the OMOP path replaces).

### Option 3 — Source (CB/OMOP) owns the crosswalk *(authoring layer — adopted)*
The side that owns the trial vocabulary authors and curates the CB↔OMOP mapping; consumers receive the result.
- **Good:** one source of truth; provenance / versioning / clinical review in one place; OMOP-native.
- **Verdict:** **adopted as the authoring layer.** Realized as CB-owned curation (CSV + `TherapyOmopMapping`), not a live network service.

**Options 1 and 3 are not competitors — they are different layers.** Authoring at the source (3) + distribution via materialized columns (1) is the as-built design.

### Option 4 — Dedicated ETL service that rewrites the whole trial corpus into CTOMOP vocab
A standalone ETL *replaces* every trial's fields with CTOMOP vocab so EXACT needs "no crosswalk."
- **Bad:** the ETL **is** the crosswalk, relocated and made **destructive**; it loses the CB source criterion (not re-derivable after a fix); blast radius = the entire corpus; partial concept coverage forces a mixed-vocab corpus for years; re-keying breaks the legacy therapy→component→category expansion and cannot express no-map / "A or B" composites without porting the whole taxonomy to OMOP.
- **Verdict:** **rejected** (Codex + eng review). Distinct from Option 1: Option 1 *adds* concept_id columns beside the CB fields (non-destructive, source preserved in CB); Option 4 *replaces* the corpus. A safe ETL that preserves raw CB collapses back into Option 1+3.

## Decision — the design

**Authoring (CB) + materialized distribution columns (CB-filled) + a flag-gated matching seam (EXACT), with all `CB↔HemOnc` mapping kept out of EXACT.** §1–§2 are as-built; §3–§4 are the decided target (the current `feat` branch differs — see "Delta from the current feat-branch implementation"). Concretely:

### 1. The crosswalk lives in CB and is curated there
- Built offline **in CB** by `cancerbot/docs/omop/mapping/build_mapping.py`: name-match CB vocab titles against pinned Athena/CTOMOP dumps (RxNorm drugs, **HemOnc** regimens, drug-class categories) + hand-curated overrides + explicit `NO_OMOP` (procedures). `build_mapping.py` itself does **no** LLM step; the `llm`-tagged rows are LLM-proposed and merged into the CSV by a separate curation step. **EXACT vendors only the resulting CSV** (`docs/omop/mapping/therapy_omop_mapping.csv`) and loads it via `load_therapy_omop_concept_ids` (which accepts `auto`/`curated`/`llm` rows).
- Serialized to `docs/omop/mapping/therapy_omop_mapping.csv` (~328 rows), materialized into two models (ported to EXACT, #4476): **`TherapyOmopMapping`** (`level`, `cb_code`, `omop_concept_id` nullable, `omop_name`, `omop_vocab`, `match`) and **`OmopConcept`** (`concept_id → concept_name/vocabulary_id`).

### 2. Concept_ids are materialized onto the trial rows (CB fills them)
- `omop_concept_id` (nullable, indexed) on the vocab models `Therapy` / `TherapyComponent` / `TherapyComponentCategory` is the source of truth.
- 10 `omop_*` JSONB array columns on `Trial` (5 required/excluded pairs) hold concept_id **strings** (so the existing `has_any_keys`/GIN overlap filters carry over), backfilled from the vocab `omop_concept_id` via `omop/therapy_concept_mapper.py` + a locked writer (`therapy_sync`). GIN pair indexes per required/excluded pair.

### 3. EXACT matches on concept_id overlap behind a one-place seam
- `TherapyMatchProfile` (`trials/services/therapy_match_profile.py`) holds **only the trial-side column names**; the queryset and matcher read columns via the profile. Two instances; `EXACT_OMOP_THERAPY` (env, **default OFF**) selects legacy vs OMOP. Flipping the flag swaps which columns are read — no matching-logic change.
- When ON, matching per level:
  - **regimen + drug-component:** **direct `concept_id` overlap** — patient concept_ids ∩ the trial `omop_therapies_*` / `omop_therapy_components_*` columns. **No EXACT-side resolution** — both sides are already concept_ids.
  - **type (drug class):** EXACT holds **exactly one** CB-generated lookup, `component_concept_id → CB category code`; the patient's component concepts resolve to CB category codes, overlapped against the trial's legacy `therapy_types_*` (CB codes). This is the **only** crosswalk knowledge in the EXACT codebase.
  - `planned_*`/`supportive_*` stay legacy until their vocabs gain concept_ids.

### 4. EXACT does NOT own the CB↔HemOnc mapping; the patient arrives pre-expanded
EXACT is stateless and owns no CB↔HemOnc crosswalk. The full `CB code ↔ HemOnc/RxNorm concept` mapping (therapy + component) lives **only in CB**, where it fills the trial `omop_*` columns. **CTOMOP supplies the patient pre-expanded to regimen + component `concept_id`s** — regimen ids are already on `PatientInfo` (PR #168); component (drug) concepts already exist in CTOMOP's OMOP `DrugExposure` (`drug_concept`, RxNorm) and are surfaced on the `PatientInfo` contract EXACT reads. So EXACT does **pure concept overlap** for regimen + component and holds **no reverse-resolution logic** — the only mapping it consults is `component_concept_id → category` (for types). The trial-detail response additively attaches `omopConcepts` (`[{code,title,vocab}]`, via `OmopConcept`).

### Direction (corrected)
Under OMOP, both sides speak **OMOP concept_ids** for regimen + component (not "patient → CB vocab"). Types are the one CB-hierarchy construct: patient component concepts → CB category codes (one lookup) → overlap the CB-code `therapy_types_*` column. Legacy mode keeps internal-code matching with full expansion; the two coexist behind the flag, and legacy-mode EXACT stays byte-identical to CB.

## The crosswalk shape, as built

The CSV seed header (`cb_title` is CSV-only; the `TherapyOmopMapping` model omits it):
```
level            # regimen | component | category   (category level slated for removal — decision A)
cb_code          # CB internal code  ── model: unique together (level, cb_code) → 1:1 per code
cb_title         # CSV only (human label; not stored on the model)
omop_concept_id  # nullable: null = no clean standard concept (expanded into components/classes at backfill)
omop_name
omop_vocab       # RxNorm | HemOnc | drug-class
match            # auto | curated | llm | needs_review | no_omop   (provenance / coverage status)
```

It is a **flat 1:1 per-code map with provenance**, not a relationship-typed graph. Key consequences:
- **No relationship typing.** Instead of `exact/narrower/broader/lossy`, the design uses `match` (provenance/confidence) + `null` concept (no clean mapping) + **fan-out at backfill** (a regimen with no single concept is expanded into its component/class concepts). Unmapped rows are retained and queryable (coverage is visible, not silently dropped).
- **1-to-many collapses are accepted, not typed.** `vrd` and `vrd_lite` share the same drug set and map to the same HemOnc concept → under OMOP they **collapse to one concept** (the dose distinction is lost). The design accepts this; `shadow_compare` ranks such divergence-risk codes by trial frequency to drive SME review. (Relationship typing — to *represent* such lossy collapses instead of accepting them — is part of the future generic vision, not the therapy as-built.)

## Hierarchy: keep ours; the only EXACT crosswalk is component → category

HemOnc's class structure fits our category hierarchy poorly, so **we keep our own hierarchy** and push all `CB↔HemOnc` mapping out of the EXACT codebase:

- **`therapy` (regimen) and `component` (drug) map 1:1 to concept_ids — in CB.** CB fills the trial `omop_therapies_*` / `omop_therapy_components_*` columns; **CTOMOP** supplies the patient's regimen + component concept_ids. EXACT never resolves either — pure overlap on both sides.
- **`type` (category) is the one thing EXACT knows.** Types have no usable OMOP concept (below), so they stay a **CB-hierarchy construct**. EXACT holds **one CB-generated lookup, `component_concept_id → CB category code`**: the patient's component concepts resolve to CB categories, overlapped against the trial's legacy `therapy_types_*` (CB codes). The category linkage is CB-authored (`TherapyComponentCategoryConnection`); EXACT just reads the compiled `component→category` table.

Net effect: **EXACT holds no `CB↔HemOnc` therapy/component mapping and no reverse-resolution** (the current `omop/therapy_graph.py` regimen→internal→graph walk is removed). The only crosswalk in EXACT is `component→category`.

### Why types are NOT OMOP-mapped (decided A)

Two reasons, both decisive:
1. **No usable type concept.** Reaching a class concept means a CB-category → HemOnc/ATC-class mapping — the "HemOnc categories fit our hierarchy poorly" problem (those category rows are mostly hand-`curated`, and ~9 of ~30 have no clean class concept at all).
2. **The patient never carries a type concept.** CTOMOP supplies regimen + component concepts; types are always derived as **CB category codes**. So an `omop_therapy_types_*` column holding HemOnc class concepts (e.g. `proteasome_inhibitor → 35807295`) is **unmatchable**, not merely unused — patient types are CB codes and can never overlap a class concept.

**Decision (A):** types are not OMOP-mapped. **Drop** the dead `omop_therapy_types_*` columns + the `category` level from `THERAPY_LEVELS` and the CB pipeline. Types match in CB-category-code space, via the `component→category` lookup, in both modes. (Rejected alt "B-components" — stuff class-member component concepts into `omop_therapy_types_*` — adds backfill+matcher rework and a fail-closed guard; revisit only if a third-party replica must match types in pure OMOP.)

### How search/matching works under the decision

Under `EXACT_OMOP_THERAPY=ON`, patient on RVd supplies regimen `[35806260]` + components `[1336825, 19026972, …]` (from CTOMOP):

| Level | Patient values (from CTOMOP) | EXACT step | Trial column (profile) | Overlap space |
|---|---|---|---|---|
| regimen | regimen `concept_id`s | none (direct) | `omop_therapies_*` | OMOP concept_id |
| component | component `concept_id`s | none (direct) | `omop_therapy_components_*` | OMOP concept_id |
| type | component `concept_id`s | `component→category` lookup → CB codes | legacy `therapy_types_*` | CB code |

- **Search (queryset):** `eligible_for_*` read the column names via `THERAPY_MATCH_PROFILE` and filter with `has_any_keys` overlap (+ the `has_no_prior_therapy` empty-list early return). Regimen/component filter the `omop_*` columns directly; types filter the legacy column after the one `component→category` lookup.
- **Match (matcher):** `get_overlap` per level → `matched / not_matched / unknown`. E.g. patient components `{1336825, …}` → categories `{proteasome_inhibitor, …}`; trial `therapy_types_required = ['proteasome_inhibitor']` → matched; excluded overlap → hard reject.

Net: regimen + component match in **OMOP concept_id space with zero EXACT logic**; types match in **CB-category-code space** via the single `component→category` lookup.

### Delta from the current feat-branch implementation

The `feat/omop-crosswalk-exact` code today does this differently and must change to reach the decision:
- **Today:** patient arrives regimen-only; `omop/therapy_graph.py` reverse-maps regimen `concept_id` → internal `Therapy` (via `Therapy.omop_concept_id`) → components → categories (all CB M2M). `omop_therapy_types_*` is built (dead).
- **Target (decided):** CTOMOP surfaces component concepts on `PatientInfo`; EXACT drops `therapy_graph` reverse-resolution and does pure overlap for regimen+component; adds a compiled `component→category` lookup for types; drops `omop_therapy_types_*` + the `category` pipeline level.

## Distribution: read-only replica

The `omop_*` trial columns ARE the integration contract. Other projects read the trials DB replica and get concept_ids in SQL, GIN-indexed, with no resolver. This is why materialized columns (Option 1) are required, and why a boundary-resolver or ETL would be harder/unworkable for a pure SQL consumer.

## Rollout safety (as built + plan)

- **Default OFF.** `EXACT_OMOP_THERAPY` is off everywhere; legacy parity is byte-identical to CB and CI-guarded (drift detector + hardcoded-field scanner + parity test, per the porting doc).
- **Cutover gate:** `omop/shadow_compare.py` re-derives expected `omop_*` from legacy and reports (a) **drift** (stored vs recomputed → stale backfill) and (b) **divergence risk** (legacy codes with no OMOP concept → would match differently after the flip), ranking top unmapped codes by trial frequency.
- **Order:** CB ships + backfills columns; **EXACT flips first** behind the flag; dual-field period; CB drops legacy columns only after all EXACT envs are cut over.
- **Production write path:** in split-DB prod, CB ships populated columns; EXACT has no live `post_save` sync (only a local backfill command).

## What's left (finish, not build)

- **Data:** SME-curate the ~50 `needs_review` codes; pin a real CTOMOP release. Until then backfill writes empty arrays for those.
- **Cutover:** drive `shadow_compare` to clean, then flip the flag per environment.
- **Demographics:** OMOP gender/ethnicity (gender + ethnicity→RACE concepts) is built in CB but **not yet ported to EXACT** (`shadow_compare` notes therapy-only).
- **Levels:** `planned_*`/`supportive_*` await concept_ids in their vocabs; `therapy_types_*` intentionally stays legacy (CB category codes).

### Migration to the decided minimal-EXACT design (delta from `feat/omop-crosswalk-exact`)
1. **CTOMOP (under our control):** surface patient **component (drug) concept_ids** on the `PatientInfo` contract (data already exists in OMOP `DrugExposure.drug_concept`, RxNorm), alongside the regimen ids (PR #168).
2. **CB:** generate + ship a compiled **`component_concept_id → CB category code`** lookup (from `TherapyComponentCategoryConnection`), regenerated on component↔category changes, `shadow_compare`-guarded like the columns.
3. **EXACT:** remove `omop/therapy_graph.py` reverse-resolution; regimen+component = direct overlap on the patient-supplied concepts; add the `component→category` lookup for types.
4. **EXACT:** drop `omop_therapy_types_*` columns + the `category` level from `THERAPY_LEVELS` and the CB pipeline; `makemigrations --check` after dropping the columns + their GIN indexes.

## Generic cross-vocabulary mapping — non-therapy domains (future, NOT yet built)

The other ~14 domains (markers, conditions, staging, grades, labs, receptor status, …) still go through the brittle name-string `normalize_ctomop_row` path. Extending the OMOP approach there is **future work** and is where the original generic-crosswalk ideas apply:
- a generic, versioned, reviewed mapping store with **`relationship` typing** (`Maps to / narrower / broader / lossy / no-map`) so lossy collapses are *represented*, not just accepted;
- `unmapped` ≠ `absent` end-to-end; fail-closed on ambiguity;
- a second consumer (**SoC**, the therapy recommendation engine, currently on its own RxNorm `_*_codes.py` bridges — see SoC ADR 0001 / #198) reading the same artifact.
These are not decided for therapy (which is intentionally simpler: 1:1 + `match` + accepted collapse) and should be revisited per domain.

## Invariant

> No unresolved or lossy fact may silently strengthen eligibility.

Acceptance test for every change here. The OMOP therapy path honors it via default-OFF + `shadow_compare` divergence-risk gating before cutover; the legacy path violates it today (the bugs below).

## Known correctness bugs in the legacy (name-string) bridge

On the `ctomop_adapter` path (not the OMOP concept path), verified, tracked in #174 — clinical-safety (false eligibility from information loss):
- `resolve_code_csv` silently drops unresolved items from multi-value fields → an unmapped **excluded** fact disappears → false `eligible`. (verified)
- `eligible_for_required_and_excluded_lists` returns `self` (no filter) on an empty/unmapped value list → all candidates pass the prefilter. (verified)
- Unmapped scalar → `None` → aggregate reports `potential`, not a mapping failure. (consistent with code)
- `_match_type_bool_restriction` (`user_to_trial_attr_matcher.py:731`) turns a missing value into `False` → `not_matched` (exclusion) rather than `unknown`. (verified)
- Process-lifetime cached title mappings (`_build_code_lookup`) go stale after a taxonomy update. (flagged)
