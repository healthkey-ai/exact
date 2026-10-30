# Typed TrialPredicate / PatientEvidence v1 (#660)

**Contract-development coverage, not clinical or deployment readiness.** No
request route imports this module. No evaluator, PatientRecord adapter, vocabulary
mirror, hierarchy expansion, dependency-pin update, migration, write, backfill or
flag activation is added. #661 owns readers, #662 owns trusted readiness binding,
#663 owns validation of the integrated paths. CB publication freshness is #5616.

## Ownership and inspected revisions

| Surface | Inspected immutable revision | Actual responsibility / capability |
|---|---|---|
| CB `dev`, merged #5615 | `480dbf53a1e544911cb611baca9a67c3a1b715f1` | All-55 registry and typed Trial compiler/exporter; **no** exact-matching/promop-omop dependency in this branch's requirements |
| CB `promop` integration consumer | `78b91f66ce0d7fa8e80f2d4162fed10a8c2107c3` | Consumes EXACT `ec97110ca1080a73533f7043ba4a3ecaeb920361` (`matcher_app`) and PROMOP `c7138fab05a4e2fe865d0f210770aaf927fb13ba`; does **not** consume this unified contract |
| EXACT `2omop` base | `a0d256902bb658e78a49d542b2caf8c954de4290` | Existing `exact_matching.patient_record.PatientRecordSurface`; #658/#659 catalog/schema repair; existing therapy/language paths, not all-55 readers |
| PROMOP `cb-v2` | `c7138fab05a4e2fe865d0f210770aaf927fb13ba` | PatientRecord-first scalar writes, dedicated clinical inputs/Genomics and clinical facts; no assertion here that flattened projection exposes all required evidence |

CB owns criterion meanings, rule thresholds, authoring and Trial storage. The
existing shared `exact_matching` package owns this contract and eventual matching
behavior, not a second evaluator beside it. PROMOP owns clinical evidence and its
projection. Hosts retain authorization/person binding, schema, geography and
transport; CB's `PackagedMatchingSurface` also supplies its attributes factory.
The new module imports no host or PROMOP models. It does not replace the existing
surface or make its transitional reverse mappings the end-state architecture.

The current CB `promop` exception receipt permits **only** PROMOP `c7138fab…`,
reachable from `cb-v2`, owner `LeoMo42`, issue #2114, expiry `2026-11-27`.
Its EXACT receipt permits `ec97110…`, reachable from `2omop`, owner `LeoMo42`,
issue #656, expiry `2026-12-31`. These are inspected integration-branch receipts,
not CB `dev`'s obsolete entries and not blanket authority for a future pin.
PROMOP work may target **`cb-v2` only**. No `cb-v2 -> dev` promotion or pin-policy
weakening is a prerequisite we can perform; a new SHA needs its own review and
applicable approved receipt. Merge authority is not migration/deploy authority.

Some older CB architecture documents describe future or historical layouts.
This inventory distinguishes those proposals from actual requirements and host
wiring at the revisions above. In particular, the old patient-side draft's
PATCH-to-OMOP description is superseded by PROMOP's PatientRecord-first document.

Related contracts: [PatientRecord read contract](patient-record-read-contract.md),
[CB/EXACT attestation seam](adr/0003-cb-exact-convergence-attestation-seam.md), and
[patient expansion ownership](adr/0004-patient-expansion-tell-dont-ask.md).

## Executable schemas and validation

`matcher_app/exact_matching/contracts/` contains:

- `inventory.py`: explicit frozen 55-entry scope, producer identities, native
  representation/operator/role, matcher operand, required patient operands,
  companion/group closure, applicability source, semantic owner, physical evidence
  route and unresolved gates. It is a **snapshot**, not a new mapping authority or
  live compiler. `SOURCES` distinguishes a required evidence route from a claim
  that every field already has an approved recipe or exporter.
- `schemas.py`: Draft 2020-12 definitions, local references only. `schema_for`
  returns an independent JSON schema document. Unknown fields and grammar versions
  are rejected. Standalone JSON Schema validation is shape-only; relational checks
  additionally require `validate`.
- `validation.py`: optional offline structural/reference validator and immutable
  identity claims. Install `exact-matching[contracts]` to use it. It checks bounded
  JSON data (maximum depth 32 / 50,000 values), native role/operator/field grammar,
  complete dependency names, question/value types, physical literal/primary-binding
  consistency, count identity and same-person/parent references. It returns no
  clinical verdict and touches no database or remote schema/vocabulary.
  The contracts extra includes the RFC3339 checker explicitly; invalid or
  offset-free timestamps are not silently accepted by an optional-format fallback.
  Evidence-reference cycle detection is iterative: a flat reference graph is not
  limited by Python's recursion depth or mistaken for deeply nested JSON.

```python
from exact_matching.contracts import MatchingContractContext, validate, schema_for

context = MatchingContractContext.from_claims(context_document)
validate(trial_document, 'TrialPredicate', context=context)
validate(patient_document, 'PatientEvidence', context=context)
# This is NOT an eligibility result, provenance verification, or readiness token.
```

`validate` without a context is useful for standalone schema development, not
request identity binding. Future consumers must pass the one resolved request
context **after #662 has verified its producer receipts**. Error codes contain no
patient values. Even successful validation of `assurance='producer_reported'` only
means the claim is structurally representable. Synthetic/unverified claims never
become trusted because their JSON is valid.

## TrialPredicate: native storage, not a normalization compiler

The wrapper schema is `exact-trial-predicate-1`. `field` is one of the 55 explicit
source fields (not an inferred family or arbitrary `omop_*` column name).
`native` retains exactly the current CB wire shapes:

| Representation | Native shape | Meaning preserved |
|---|---|---|
| Simple concept set | `["concept_id", ...]` | Approved target identities at the producer's reviewed level; no consumer hierarchy expansion |
| Language/skill | `["language_id:skill_id", ...]` | Pair association, not two independently intersected sets |
| Structured field | `[{schema: "cb-trial-criteria-1", role, operator, terms, ...}]` | Native envelope, not an artificial flat concept list |
| No constraint | `[]` with `state="unconstrained"` | Only with all companion/group dependency claims current and compatible |
| Mapping/capability/freshness failure | `native=null`, `state="blocked"`, explicit reason | Never interpreted as no constraint or patient negative |

Every wrapper supplies the complete `dependencies` closure, current source hashes
and authoring generation, typed `source_companions`, answer-binding claims and
provenance. Referenced generations must equal the bound context's authoring
generation. Supplied hashes/generations are **not proof** of freshness; #662 must
bind them to actual CB publication, including invalidation on authoring changes.

SCT can have `terms=[]` and **still constrain** through `independent_requirements`:
history and `minimum_elapsed_days >= 0` combine with the exclusion verdict by
`and_with_exclusion_verdict`, anchored to `evaluation_date`. A minimum of zero
does not disappear. Unavailable/stale siblings cannot certify an empty field.
"Recent" maximum/anchor authority remains CB #5354; this grammar does not invent
one. Concomitant washout with a non-null duration stays blocked on CB #5353; no
new equality operator is silently chosen.

### Subjects, asserted findings, statuses and linked qualifiers

- `genomic_subject` names a subject, **not a positive result**: `status_filter=null`.
  Gene/variant axes remain `cross_axis_combination="independent_exists"`.
- A bound origin/interpretation carries `qualifier` with its own typed question
  and answer, associated with that **same finding**. This does not force all axes
  to match the same finding across fields.
- `finding` explicitly requires a string `present` result on the associated
  status question. A question/test concept alone cannot stand in for that result.
- `assertion/state="encoded_by_concept"` preserves a concept whose meaning already
  encodes status, including negative HER2. No positive status is overlaid.
- `coded_result` binds question, physical answer and `same_observation` together.
- `condition` retains explicit activity/neoplastic qualifiers and index-malignancy
  association where emitted. A scalar CNS question without its approved result
  binding remains blocked (CB #5352).

### Counted and derived rules

`rule_identity` is the source criterion's identity, not a reverse patient code.
Two source rules mapped to the same concept still count separately; duplicate
identities within one count scope are invalid. Reusing the same evidence for
different rules is not prohibited. MCL's native emitted minimum is **at least 1**:
CB transforms source companion `0` using `(minimum or 1)`; excluded and
sufficient-any use 1. The original companion remains separately visible.
`required_or_sufficient_any`, group identity and `exclusion_overrides=true` are
preserved literally. No clinical threshold or combination rule is revised.

Numeric comparisons carry typed/unit-bearing questions. MIPI/SMIPI expression
trees retain their algorithm version, default operand and missing/nonpositive
dependencies. Operators have specific arity/types; invalid arithmetic syntax is
rejected, but nothing evaluates the formula here. A valid expression does not
prove it was the authorized source formula: trusted publication must bind its
bytes to the reviewed producer. Complex-karyotype recipes remain CB #5343.

## Physical answers: display is not code, code is not approval

At the inspected PROMOP SHA, actual `omop_core/services/omop_projection.py`
`project_single_value` clears old number/string/concept answer columns first:

- String/date/json routes store the **literal** string. CB's string binding uses
  the exact PROMOP VocabularyLookup `title`, not its code or a guessed alias.
- Concept-valued routes store the display string and look up that complete string
  in `value_concept_map`. `write_descriptor.py:516–545` builds that map from primary
  `FieldChoiceCode` rows. Missing/conflicting primaries are not a positive answer.
- Concept-valued multiselect performs a lookup of the whole comma-joined string,
  **not** of each member. This contract rejects claims of member-level concept
  support for such a writer. String membership is allowed only with the explicit
  `comma_joined_member` rule and an unambiguous comma-free literal.

`answer_bindings` carries catalog table/row/code/title, question recipe, multiplicity,
source mapping ID and primary-code claims. The validator cross-checks literal
titles, primary target IDs/vocabulary/codes and receipt references. It cannot
verify that those rows exist or that omitted conflicting rows were absent.
Exact physical bindings and primary codes do **not** grant clinical SCCM approval;
the independent approved mapping and certified release gates still apply
(PROMOP #1224, CB #5619). No patient concept is reverse-translated to a CB code.

## PatientEvidence: state, availability, completeness and selected evidence

`exact-patient-evidence-1` separates these dimensions:

| Dimension | Values |
|---|---|
| Clinical state | `unanswered`, `present`, `asserted_none`, `negative`, `indeterminate`, `conflict`, `cleared`; independent language capabilities may be `mixed` |
| Evidence availability | `available`, `unavailable`, `unsupported`, `missing_mapping` |
| Declared scope completeness | `unknown`, `partial`, `complete` |

An empty result, empty collection, numeric zero, boolean false, null, absent
mapping and absent Trial constraint are different things. No truthiness conversion
joins them. Negative/none claims require selected evidence and known declared
scope, not the absence of a DrugExposure, Condition or Genomics row. `conflict`
retains multiple underlying selected facts; the schema does not adjudicate them.

Each document includes the authorized opaque `person_key`, disease/index-event
scope, as-of time, facts, explicit `current`/`superseded` reference sets, issues and
provenance. These are producer selection claims, not a new EXACT reconciliation
algorithm. All facts must be referenced once by those sets. #662 must establish
person authorization, source snapshot/selection identity and completeness rather
than trusting client-chosen keys or `completeness="complete"`.

Evidence variants:

1. **Physical OMOP fact:** table, row ID, person key, source key, concept ID
   (including legitimate unmapped **0**), time, raw answer columns, erroneous=false
   and, for a linked component, its Measurement parent and typed CDM event-field
   identity. Same-person parent must exist in the evidence document; a coincident
   ID from another table is not its parent. A concept-0 row can be retained, but
   cannot become an approved Trial target or silently acquire a feature concept.
2. **PatientRecord assertion:** record/field/revision identity, typed saved value,
   time and projection state `pending`/`unavailable`/`acknowledged`. It requires no
   fabricated OMOP row or Genomics parent. An unprojected edit can supersede an
   older physical fact. Clear is explicit, not an invitation to fall back to an
   older row. A raw `derived_at`/`derivation_version` does not identify a direct
   edit because direct saves do not advance those columns.
3. **Language capability:** record/field/revision plus language/skill IDs and
   true/false/null, preserving answered versus never-asked. The existing EXACT
   pair-builder exception remains unchanged: only held speak/write capabilities
   produce pairs; any known capability marks its language asked. Moving pair
   production requires an explicit producer/consumer change, not this contract.
   Different independent skills with different states have aggregate `mixed`,
   not `conflict`; contradictory current answers for the same language/skill
   pair have `conflict`. This is structural grouping, not an eligibility verdict.
4. **Derived evidence:** owner, algorithm version, input evidence references and
   typed result. Cycles/missing inputs are invalid. No formula runs here. Stored
   PROMOP-derived fields must not be replaced by a second formula (#657).

Event-field references must agree with `measurement.measurement_id`: either the
legacy table.column concept code or an Athena `CDM###` code whose declared concept
name resolves that column. An explicit Observation-column code does not become a
Measurement reference merely because `vocabulary_id='CDM'`. Verifying the real
concept row/name/standard status against the vocabulary is still #662, not trust
in a client-supplied name.

### PROMOP semantics checked in source

Read on **`cb-v2@c7138fab`**: `docs/patient-record-first-writes.md`,
`genomics_architecture.md`, `field_concept_mapping_architecture.md`, and
`therapy-reference-tables-architecture.md`. Actual paths inspected as well:

- `omop_projection.py`: descriptor-driven scalar projection, separate source key,
  same-day upsert, clear tombstone `PatientRecord:cleared`, pending-edit protection;
  occurrence tables cannot manufacture null/negative occurrences.
- `write_descriptor.py`: physical display-to-primary-concept lookup; no per-member
  concept multiselect support is established by primary rows alone.
- `genomics.py`: `save_variant`, `_components`, `enrich_variants`; Measurement
  parents and same-person, event-type-checked linked Measurement/Observation
  components; source-only writes are legitimate and components are physically
  strings unless numeric. `patient_record_service.py::_get_genetic_mutations`
  exposes a flattened projection with finding ID/state/text, not every physical
  child row/source/mapping identity. No standard OMOP Genomic subject identity is
  proved by an uppercased gene string.
- `therapy_history.py`: explicit `TherapyHistoryAttestation` distinguishes complete
  lists from lower bounds. Reference regimen/component/class rows do not prove
  that the patient received a regimen. Existing assertions do not establish all
  planned/supportive/concomitant physical mappings (#1070/#1072/#1924).

This is source-level producer-semantics verification, **not** a live roundtrip or
target vocabulary capability audit. Direct edits still write PatientRecord first;
this contract must never turn consumers into OMOP endpoint writers.

## Coverage versus unresolved gates

All 55 fields remain in scope. `inventory.CRITERIA` is the machine-readable
field-by-field inventory; `source_operands`, `recipe`, `dependency_closure`,
`applicability_source`, `evidence_source` and `actual_consumer_capability` expose the
required columns without duplicating the compiler. Applicability comes from the
explicit existing matcher config at the stated EXACT base, not invented clinical
scope; planned/therapy lineage has no disease restriction in that path. It does
not certify the future authoring UI's applicability.

| Gate | Owner / tracker | What remains distinct from grammar coverage |
|---|---|---|
| Missing/proposed/invalid concepts | CB #5619 / PROMOP #1224, #1229 | Reviewed mappings and current certified release membership |
| CNS answer/context | CB #5352 | Approved question/result and index-disease binding |
| SCT recency | CB #5354 | Authorized maximum/anchor, not elapsed-minimum substitution |
| Concomitant washout | CB #5353 | Equality boundary authority shared by both readers |
| Complex karyotype | CB #5343 | Actual counted finding recipe, not test-concept existence |
| Malformed cells / therapy conflicts | CB #5617 / #5618 | Explicit remediation authority, not validation-time coercion |
| Authenticated patient evidence export | [PROMOP #2124](https://github.com/healthkey-ai/promop/issues/2124), `cb-v2` only | Physical bindings, linked IDs, pending/clear selection, capabilities and snapshot receipts; flattened strings are insufficient |
| Stored-derived ownership | EXACT #657 / PROMOP #1981 | No duplicate derivation or silently negative unknown |
| Current all-55 materialization | CB #5616 | Generation publication, dependency closure, authoring invalidation; a fill-only seeder is not it |
| Integrated native/package/host readers | EXACT #661–#663 | Final evaluation before pagination/count/ranking when SQL prefilters approximate |

Open tracker status alone is not evidence that every described deficiency still
exists at the inspected SHA. Where a mechanism already exists (history
attestation, source-only Genomics, pending edits), the remaining gate is evidence
export/verification, not a request to recreate it. No blanket "all OMOP supported"
or deployment-ready claim follows from these schemas.

Concrete recipe asymmetry at the inspected compiler: `ANSWER_REFERENCE_TABLES`
does not name literal catalogs for disease behavior/subtype or MIPI/MIPI-c risk.
Those coded-answer routes cannot be made ready by guessing a table. An approved
encoded finding is a separate representable route where semantically applicable;
it is not blanket approval. The inspected PatientRecord has no stored MIPI/MIPI-c
risk column; current matcher-side formulas and their ownership remain explicit.
The bulky-size inventory is separate from MCL's wider rule operands: it does not
acquire a complex-karyotype gate merely because both use counted-rule syntax.

## Synthetic fixtures and verification boundary

`tests/contracts/fixtures.py` labels every receipt `synthetic`. All 55 entries have
native-shape and blocked/unconstrained examples. Tests cover encoded/string/concept
statuses, catalog title versus code, missing/conflicting primary bindings,
unsupported concept multiselect, linked Genomics and source-only 0, independent
axes versus same-finding qualifiers, pending clear over old OMOP, count identity,
source minimum 0/native minimum 1, empty SCT terms with independent requirements,
stale companions, operation arity/types, language asked state, immutable request
identities, malformed/unsupported versions and value-free errors.

The offline source compatibility probe additionally executes the **actual pure CB
compiler** against its own synthetic 55-field fixtures at `480dbf53a` (the three
inspected compiler/registry/rule files match that commit). This is not a snapshot
materialization, patient test or approval. No runtime dependency on the CB source
checkout is introduced into this package or CI.
