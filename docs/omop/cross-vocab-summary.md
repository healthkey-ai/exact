# Summary — Cross-vocabulary therapy matching (CB ↔ OMOP ↔ EXACT)

_Session synthesis, updated 2026-07-22 (supersedes the 2026-06-19 snapshot). Companion to ADR 0001 (`docs/adr/0001-cross-vocabulary-mapping.md`) — see the **ADR status** note under Artifacts: the as-built ADR is on `main`, this branch still carries the earlier draft._

## Problem
EXACT (trial matcher) and SoC (therapy recommendation engine) must match patients whose data arrives in PROMOP/OMOP vocabularies (HemOnc regimens, RxNorm drugs) against trials authored in CancerBot's internal codes — on a shared vocabulary, so a stateless EXACT and external patient sources interoperate.

## What's actually built (the big correction)
This is **not greenfield**. A coordinated epic is already implemented: **CB #4447 → ported to EXACT**, behind flag **`EXACT_OMOP_THERAPY` (default OFF)**, landing across the crosswalk feature branch (PRs #192–#208, #4476) and continuing on the current OMOP branch (supportive #228 / #4449 / cb#4590, demographics #229, types-drop #4502). **Not yet merged to `main`.**

The design:
- **CB owns the crosswalk.** Built in CB by `build_mapping.py` (name-match Athena/PROMOP dumps + curated overrides + `NO_OMOP`; LLM rows merged separately) → CSV (`match` provenance: auto / curated / llm / needs_review / no_omop) → `TherapyOmopMapping` (1:1 per `(level, cb_code)`) + `OmopConcept`. EXACT vendors only the CSV.
- **CB materializes `omop_*` concept-id columns on trials** — **regimen, component, and supportive** therapy levels — plus the demographics columns (below). EXACT reads them; it never writes trial data.
- **EXACT matches on concept_id overlap** behind the `TherapyMatchProfile` seam (`trials/services/therapy_match_profile.py`), which flips legacy↔omop column names with no logic change. Under the flag the **trial-side** profile flips **regimen + component + supportive**; **types and planned stay on legacy CB-code columns** (see Decision A and the planned/types note).
- **Supportive is a half-open seam.** The trial columns are materialized (cb#4590) and the profile flips supportive **reads** under the flag (#228), **but the patient-side PROMOP adapter does not yet emit supportive concept_ids** — `normalize_promop_row` converts only the therapy-line fields (`*_therapy_id`, `later_therapy_ids`) and leaves `supportive_therapies` as legacy codes. So with the flag ON today the patient supplies CB codes while the trial column holds concept_ids → **no overlap, supportive constraints silently drop**. This is an **open cutover gap** guarded by the #221 coverage gate; the flag stays OFF on prod until the adapter emits supportive concept_ids and coverage is validated. (See the code-inconsistency note at the end.)
- **Patient comes as regimen concept_ids only** (PROMOP PR #168). EXACT derives components (concept_ids) and types via the **CB graph** `Therapy→Component→Category` (`therapy_graph.py`). No EXACT-side patient crosswalk.
- **Demographics (gender/ethnicity) are ported and in-tree** (#229): `Ethnicity.omop_concept_id`, `Trial.omop_ethnicity_required` (JSONB+GIN), `Trial.omop_gender_concept_id`, the `omop/demographics*.py` mapper/profile/sync, the loader + backfill commands, and `shadow_compare` coverage. The `DemographicsMatchProfile` **intentionally still points at the legacy columns** — the demographics infra is materialized and shadow-compared but the read-cutover is deferred until PROMOP emits race/gender **concept_ids** (today it emits `M/F/U` and ethnicity concept-*names*).

## Key driver: read-only replica
The trials DB is meant to be consumed as a **read-only replica by other projects**. A pure-SQL consumer can't run a resolver/ETL, so OMOP concept_ids **must be materialized columns**. This is why "columns on trials" is required (not "rejected"), and why the ETL-rewrite idea was rejected (destructive, whole-corpus blast radius, collapses back into the columns design anyway).

## Decision A — types are NOT OMOP-mapped (implemented)
HemOnc's drug classes fit CB's category hierarchy poorly, and decisively: the **patient never produces a type concept_id** — `therapy_graph` returns types as **CB category codes in both modes**. So an `omop_therapy_types_*` column holding HemOnc class concepts (e.g. `proteasome_inhibitor → 35807295`) would be **unmatchable**, not just unused.

Decided and **shipped** (CB #4502): the `omop_therapy_types_*` columns and the `category` mapping level were **dropped** from the pipeline. Types stay on legacy CB-category-code columns (identical in legacy and OMOP modes). The mapper materializes only **regimen (1:1)**, **component (1:1)**, and **supportive (1:1)**; the type hierarchy self-builds via the graph. (B-components — types as member-component concepts — recorded as the rejected alternative; revisit only if a replica must match types in pure OMOP.)

### Planned therapies — legacy, like types but for a different reason
`omop_planned_therapies_*` columns exist on the trial model but are **not backfilled** — planned is excluded from the mapper's `THERAPY_LEVELS` because `PlannedTherapy` has no `omop_concept_id`, so the columns stay empty (`[]`). The profile also **keeps planned reads on the legacy columns** (`#230` decision pending): the large majority of planned codes are drug-classes/modalities that don't map 1:1 to a concept, and flipping would map context-qualified codes (e.g. adjuvant/neoadjuvant) onto colliding concepts and silently change matching. Planned stays wholly in CB-code space until the vocab gains concept_ids.

## How matching works (flag ON)
| Level | Patient | Trial column | Overlap space |
|---|---|---|---|
| regimen | concept_ids | `omop_therapies_*` | OMOP concept_id |
| component | concept_ids (graph-derived) | `omop_therapy_components_*` | OMOP concept_id |
| supportive | concept_ids **(intended — not yet emitted)** | `omop_supportive_therapies_*` | OMOP concept_id |
| type | CB category codes (graph-derived) | legacy `therapy_types_*` | CB code |
| planned | CB codes | legacy `planned_therapies_*` | CB code |

Search (queryset, `has_any_keys`) and match (`get_overlap`) per level. Regimen and component match in concept space today. The supportive path is the dedicated matcher (#4449 — `_match_supportive_therapies` + `eligible_for_supportive_therapies`) reading the trial columns via the same profile — but its **patient input is not concept-remapped yet** (see the supportive gap above), so supportive OMOP matching is not end-to-end until the adapter is updated. Types and planned stay in CB-code space.

## What's left (finish, not build)
1. SME-curate the `needs_review` codes; pin a PROMOP release.
2. **Supportive patient-side conversion:** make `normalize_promop_row` emit supportive concept_ids (or revert the profile's supportive flip) so the supportive seam is consistent end-to-end — see the code-inconsistency note.
3. Drive `shadow_compare` clean (therapy + demographics) → flip `EXACT_OMOP_THERAPY` per environment (CB ships columns; EXACT flips first; dual-field; CB drops legacy).
4. **Planned (#230):** decide whether/how to OMOP-map planned therapies (context-collapse risk) — currently deferred, planned stays legacy.
5. **Demographics read-cutover:** deferred until PROMOP emits race/gender concept_ids; a `DemographicsMatchProfile` OMOP variant + the flag flip mirror the therapy seam when it does.

## Artifacts
- **ADR 0001** — rewritten to as-built + Decision A and **merged on `main`** (PR #214; follow-up "mark complete"). It supersedes the earlier greenfield ADR.
  - **ADR status caveat:** this branch (`2omop`) is **behind `origin/main`** on the ADR (on `origin/main` the ADR is Status **Accepted** / as-built), so the co-located `docs/adr/0001-cross-vocabulary-mapping.md` here still shows the earlier **"Proposed"** greenfield draft (with Option 1 "expand the trials DB" marked *Rejected*). That draft is stale relative to `origin/main`; this summary is the current as-built source of truth until `2omop` rebases/merges `origin/main`.
- Earlier (merged): generic-crosswalk ADR + 4-options + SoC mirror (#176/#179/#199/#205). The generic relationship-typed vision now scoped to **future non-therapy domains**.
- **SoC #198** — SoC is a separate consumer (own RxNorm `_*_codes.py`), not in the CB epic.
- Legacy name-string bridge bugs (#174) — clinical-safety, on the `promop_adapter` path, separate from the OMOP concept path.

## Open / not-code
- Planned (#230) design decision.
- Non-therapy domains (~14) still on the brittle name-string path — future.

## Code-inconsistency note (supportive seam)
The supportive seam is internally inconsistent as of this branch and needs reconciling:
- `therapy_match_profile.py` **flips** `supportive_therapies_*` → `omop_supportive_therapies_*` under the OMOP profile (#228, lines 83-84), and its inline comment claims "the consumer supplies the patient's supportive therapies as concept_ids under the flag." Note the **same file** still carries a contradicting dataclass-field comment (lines 55-58: "planned/supportive… stay legacy under the OMOP profile") — stale, fix it too.
- But `promop_adapter.py::normalize_promop_row` does **not** convert `supportive_therapies` (its comment even asserts "the OMOP profile keeps supportive/planned legacy" — stale/false w.r.t. the profile), and `therapy_concept_mapper.py` says matching "still reads the legacy supportive columns until the coordinated flip."

So the trial-side flip landed ahead of the patient-side conversion. Because `EXACT_OMOP_THERAPY` defaults OFF and the #221 gate blocks the prod flip, this is not live-breaking, but under the flag it silently drops supportive constraints. Reconcile by either (a) adding supportive concept_id emission to `normalize_promop_row` before the flip, or (b) reverting the profile's supportive flip until the adapter is ready — and fix the stale comments either way.

## Source-of-truth references
- EXACT: `docs/adr/0001-cross-vocabulary-mapping.md`, `docs/porting-from-cancerbot.md`, `trials/services/therapy_match_profile.py`, `trials/services/omop/{therapy_graph,therapy_concept_mapper,shadow_compare,demographics}.py`.
- CB: `docs/exact-downstream-and-omop.md`, `docs/omop/therapy-paths-audit.md`, `docs/omop/mapping/{build_mapping.py,therapy_omop_mapping.csv}`.
