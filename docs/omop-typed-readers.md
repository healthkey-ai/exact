# Typed readers and gated request orchestration (#661)

**Not clinical approval, authenticated evidence or activation readiness.** The
`exact_matching.typed_reader.ClaimsEvaluation` evaluates supplied #660 claims in
memory. The [request continuation](omop-typed-runtime.md) now connects it to a
bounded, off-by-default package/native runtime boundary. There is no production
resolver, new dependency pin or live activation. #661 remains open.
The existing `PatientRecordSurface` is not replaced. Claims must eventually be
authenticated by #662 and exported by CB #5616 / PROMOP #2124 before live use.

## Result contract

Each immutable `CriterionResult` contains the source `field`, a `status` and
value-free diagnostic `reasons`. `satisfied` / `violated` refer to the **constraint**
polarity; matching an excluded predicate means `violated`.
The MCL group result uses the explicit aggregate key `high_risk_mcl`, not another
source field from the 55-entry inventory.

| Status | Meaning |
|---|---|
| `unconstrained` | Validated current closure explicitly declares no constraint |
| `satisfied` | Supplied bound evidence proves the constraint |
| `violated` | Supplied bound evidence disproves the constraint |
| `patient_unknown` | Supported route, insufficient/cleared/conflicting patient answer |
| `unavailable` | Missing/blocked predicate, unavailable/missing-mapping evidence or inconsistent physical binding |
| `unsupported` | V1 has no sufficient binding/representation for the requested operation |

Malformed schemas, mismatched context/field inventory, duplicate source rules and
budget violations raise stable, value-free `ContractError`s. They are not clinical
results. Unknowns and system failures have no eligibility/score coercion.

All operands are checked before OR/count reduction. Any unsupported/unavailable
operand blocks that reduction even when another OR member is true. Otherwise
three-valued reduction counts proven-true and patient-unknown **source rules**;
true if the minimum is reached, false if true+unknown cannot reach it, unknown
otherwise. AND uses its full operand count. A missing/nonpositive rule guard
forces patient unknown even if another comparison fails, retaining native guard
semantics. No expression tree or stored-derived formula is reimplemented.

MCL combines required-minimum OR sufficient-any, then exclusion. Unconstrained
inclusion branches are absent, not automatic successful alternatives. The raw MCL
companion `0` still means emitted minimum `1`, exactly as CB's current compiler.
SCT elapsed minimum `0` is different and is never treated as blank. An unavailable
group retains a known exclusion violation as a diagnostic rather than losing it.

## All-55 evidence sufficiency matrix

The following routes describe **offline evaluability under the current schema**,
not an assertion that a producer export or approved mapping exists. Every field
also supports explicit blocked/unconstrained handling and propagates patient
availability independently. None of these rows authorizes flag activation.

| Route | Sufficient evidence / explicit limit |
|---|---|
| C | `unsupported: concept_role_binding_missing`. Flat IDs do not bind physical evidence to planned/administered therapy, regimen/component/class, ethnicity or other semantic roles. No reverse mapping is used. |
| A | Native encoded assertion plus selected physical target ID in its native domain/table. A selected negative about that exact target may falsify it; absence, unrelated targets and field-level none do not. No positive finding is overlaid on a negative-status concept. |
| Q | Exact selected physical question ID + table + source key + declared answer kind/unit. String literals and explicitly declared comma-member rules retain producer spelling; concept answers retain primary-bound target IDs and may also carry display text. A question alone does not prove an answer. |
| G | Selected genomic subject plus selected Measurement parent; status/qualifier must link to that same parent. Finding requires literal `present`; literal `absent` falsifies; other results are unknown. Subject-only existence does not require positive status. Distinct axes evaluate independently. |
| R | Count distinct source `rule_identity` values. Direct unit-bound Q numeric comparisons, A/G assertions and guarded conjunctions are evaluable. Derived scores return `stored_derived_binding_missing`; complex-karyotype recipes remain a producer gate. |
| T | `unsupported: sct_relation_binding_missing` / `sct_event_date_binding_missing`. V1 has no typed clinical event/date binding. `effective_at` is not assumed to mean transplant date. No recency rule is invented. |
| M | `unsupported: medication_context_binding_missing`. V1 does not prove concomitant/protocol scope; non-null washout remains blocked by existing validation. |
| L | Reuses existing held/asked `language_verdict` over selected capability booleans and producer-supplied pairs. No new pair construction, vocabulary lookup or skill remapping; `false` and never-asked remain different. |

| Field | Route |
|---|---|
| `planned_therapies_required` | C |
| `planned_therapies_excluded` | C |
| `therapies_required` | C |
| `therapies_excluded` | C |
| `therapy_types_required` | C |
| `therapy_types_excluded` | C |
| `therapy_components_required` | C |
| `therapy_components_excluded` | C |
| `supportive_therapies_required` | C |
| `supportive_therapies_excluded` | C |
| `pre_existing_conditions_excluded` | A for plain condition; contextual qualifiers/index malignancy return `condition_context_binding_missing` |
| `stem_cell_transplant_history_excluded` | T |
| `ethnicity_required` | C |
| `cytogenic_markers_required` | A/G |
| `cytogenic_markers_excluded` | A/G |
| `molecular_markers_required` | A/G |
| `molecular_markers_excluded` | A/G |
| `histologic_types_required` | C |
| `estrogen_receptor_statuses_required` | Q/A |
| `progesterone_receptor_statuses_required` | Q/A |
| `her2_statuses_required` | Q/A |
| `hrd_statuses_required` | Q/A |
| `hr_statuses_required` | Q/A |
| `tumor_stages_required` | Q/A |
| `tumor_stages_excluded` | Q/A |
| `nodes_stages_required` | Q/A |
| `nodes_stages_excluded` | Q/A |
| `distant_metastasis_stages_required` | Q/A |
| `distant_metastasis_stages_excluded` | Q/A |
| `staging_modalities_required` | Q/A |
| `mutation_genes_required` | G subject |
| `mutation_variants_required` | G subject |
| `mutation_origins_required` | G same-finding qualifier |
| `mutation_interpretations_required` | G same-finding qualifier |
| `concomitant_medications_excluded` | M |
| `languages_skills_required` | L |
| `stages` | Q/A |
| `binet_stages_required` | Q/A |
| `protein_expressions_required` | Q/A |
| `protein_expressions_excluded` | Q/A |
| `richter_transformations_required` | C |
| `richter_transformations_excluded` | C |
| `tumor_burdens_required` | Q/A |
| `disease_activities_required` | Q/A |
| `morphologic_variants_required` | C |
| `morphologic_variants_excluded` | C |
| `disease_behaviors_required` | Q/A; actual producer catalog binding unresolved |
| `disease_subtypes_required` | Q/A; actual producer catalog binding unresolved |
| `mipi_risks_required` | Q/A; no stored PatientRecord risk binding established |
| `mipi_c_risks_required` | Q/A; no stored PatientRecord risk binding established |
| `extranodal_sites_required` | C |
| `bulky_disease_criteria_required` | R |
| `high_risk_mcl_criteria_required` | R, required-minimum group |
| `high_risk_mcl_criteria_excluded` | R, exclusion group |
| `high_risk_mcl_criteria_sufficient_any` | R, sufficient-any group |

PatientRecord assertions retain typed pending values/clears, but **no field-name to
question/concept conversion is guessed**. Non-clear assertions return
`record_operand_binding_missing`; selected clears are patient unknown and cannot
read superseded physical evidence. Derived evidence lacks an output binding and
returns `derived_output_binding_missing`. Missing physical parents are system
unavailable, including a selected child whose parent is superseded. Claims of
`completeness=complete` alone are not per-target negative coverage.

### V1 negative-state ambiguity and scope identities

Q/R/G claims have an additional support boundary: a **used physical question
answer, genomic subject or parent** with `state=negative/asserted_none` returns
`unsupported: physical_state_binding_missing`. V1 does not distinguish denying
the observation/subject from describing its negative clinical result. This gate
does not negate an arbitrary answer and does not apply to unrelated operands.
The approved representation still supports physical `present` observations with
an exact negative-status literal/concept and linked `absent` status; subject-only
existence does not impose a positive-result filter. A's explicit negative about
the exact target, R rules using A, and L's false capabilities retain their own
semantics. This is a narrowed supported subset of v1 claims, not new clinical
polarity or an assertion that all negative results should be false.

`source_companions.disease` preserves a raw Trial authoring label; the patient's
`scope.disease_key` is an opaque identity. Their string equality/inequality is not
a disease identity mapping. Person/disease/index scope authorization and binding
remain host/#662 responsibilities before runtime use. Plain native conditions
assert only their exact target; disease-sensitive `index_disease/association` and
activity qualifiers already return `condition_context_binding_missing`. The
reader neither applies those predicates across disease scopes nor invents a
universal disease restriction on plain comorbidities. Likewise language
field-name-to-ID authenticity belongs to producer binding/#662, not guessed IDs.

## Snapshot and work limits

The session copies/validates one evidence snapshot against one immutable context,
requires a common person and scope, and keeps only per-session cached results.
Cache identity includes the full canonical predicate including dependencies and
answer-binding claims; caller mutation cannot alter previously stored inputs.
Snapshot/predicate limits are 1 MB and the #660 depth/value limits; there are at
most 55 evidence documents. Unique evaluations default to 10,000 and total cached
predicate bytes to 16 MB. Exceeding a limit raises, never truncates a count/page.
No patient, catalog or vocabulary query is made by this module.

## Runtime continuation and remaining prerequisites: do not claim #661 complete

The static Astra design review and independent Codex consult both identified:

The initial offline increment listed the items below as missing. The request
continuation implements bounded native orchestration and the agreed Python score
using upstream #5620's merged seams; see its separate scope/evidence document.
CB host presentation/pin integration, authenticated exports/publication and
target-environment/performance validation are not completed by that continuation.

1. CB-owned behavior-preserving seams for unsliced conservative candidates and
   optional supplied patient-attribute verdicts. Host visibility, study filters,
   geography, saved/favorites modes and **non-migrated scalar** constraints remain.
   Do not modify shared matcher/queryset bodies downstream first or introduce
   another adapter/evaluator to bypass this requirement.
2. Agreed grouping `55 fields -> patient attribute -> applicability -> score
   contribution`, with genomic axes/MCL/required-excluded pairs not accidentally
   becoming extra score units. Existing SQL list completeness score and Python
   detail score differ; this increment chooses neither as a new clinical policy.
   System-unavailable score/status/null-ranking policy also needs agreement.
3. A final-result contract through backend, native list/search/detail/count,
   graph, serializers, `TrialTemplates`, explanations, MCL breakdown and existing
   input/subform metadata. System problems must not become patient questions.
4. Final evaluation of the full conservative candidate scope **before** final
   counts, ranking and pagination; no page-then-filter path. Cross-host/surface
   tests and performance/query-plan measurements remain outstanding.
5. Authenticated evidence and publication binding (#662, CB #5616, PROMOP #2124),
   plus explicit clinical/mapping gates listed in the #660 contract document.

No readiness, clinical/data gate or host prerequisite is waived by synthetic
tests. This increment changes no shared matcher/queryset bodies or legacy routes.
