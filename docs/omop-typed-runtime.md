# Typed request orchestration (#661 continuation)

**Implementation under a disabled server boundary, not activation/readiness.**
`EXACT_TYPED_MATCHING_ENABLED=False` and `EXACT_TYPED_MATCHING_RESOLVER=None` are
deliberately not environment-activatable. No production resolver is supplied.
Turning on the boundary without one returns 503 rather than running legacy on
an OMOP-only request. Request JSON/query parameters cannot supply or activate a
session. #662 must authenticate person/scope/selection, publication freshness,
schemas/generations and producer receipts before a resolver can be deployed.

## Upstream and ownership

CB `51e9df25d` (#5620, merged `dev@dadadf75b`) introduced inert
`candidate_trials` / `attribute_verdicts` seams. They are ported here without
changing default filtering/matching, retaining EXACT's documented optional
patient, `therapy_id`, dispatch-table and injected-data adaptations. The full CB
backmerge #5624 is merged into `promop` as `d7c213b1`; that is not authorization
to change pins or activate this implementation.

`exact_matching.request_evaluation.RequestEvaluation` wraps the **existing**
`ClaimsEvaluation` and scalar matcher. It introduces no second evaluator,
PatientRecord adapter, vocabulary mirror, hierarchy expansion or reverse mapping.
The existing PatientRecordSurface can still be supplied by a host.

## Immutable request inputs and batch binding

- Copy the existing scalar patient surface once. Patient mutation outside the
  session cannot change a prior/future result within that request.
- Load complete, unsliced candidate rows before calling the batch producer.
  Default bound: 200 candidates. Cap+1 detects overflow; it never produces a
  partial result. Sliced/deferred querysets are refused.
- The host batch reader receives each candidate's ID and full concrete-row
  fingerprint. It must return exactly that ID set and each row fingerprint,
  including **all 55** predicates. This is structural binding, not authentication
  of a publication or a cross-database transaction.
- Copy/validate the complete inventory, context, companion and shared dependency
  claims, even for disease-inapplicable fields. Bound each row's JSON depth/value
  budget and total supplied predicate bytes to 16 MB; the batch transport must
  additionally enforce its bound before downloading/allocating data.
- Cached final results depend on the fixed scalar patient and entire concrete
  Trial row, not only typed predicates. Reusing a changed scalar row fails.
- Every producer call is batched, not one patient/publication fetch per Trial.
  The session has no process-global patient cache or writes.

## Clinical aggregation and scoring

The user chose the existing **Python/detail matched/applicable** score, zero on
proven violation. Goodness weights/formula remain unchanged.

Grouping comes from current patient-attribute config, verified to cover the full
frozen inventory. Required/excluded pairs and four independent genomic axes use
conjunction; MCL uses only `ClaimsEvaluation.mcl_group`, so a failed required
branch does not reject when sufficient-any succeeds. A known MCL exclusion is
retained even alongside a system failure. Therapy units match the Python
reference, including its four therapy-line attributes (SQL skip-in-counts does
not silently change this denominator).

SCT history and elapsed-days remain separate score positions. An unrestricted
companion in a validated **non-blocked** predicate is matched; elapsed minimum
**0** is constrained. A blocked receipt can have unavailable/stale dependencies,
so its null companion cannot certify an independent unrestricted day position.
V1's constrained SCT
route remains unsupported. Future supported SCT needs an explicit per-position
reader contribution before an aggregate can score both positions; the
orchestrator does not invent that clinical decomposition.

Only non-migrated attributes call the legacy scalar matcher. No absent override
can accidentally send a migrated system-incomplete group through legacy.
Applicable unconstrained attributes count as matched, exactly as the Python
reference. Disease applicability precedes clinical aggregation, not structural
validation.

- Proven violation: `not_eligible`, score `0`, system diagnostics retained.
- Otherwise unsupported/unavailable: explicit system status, score `null`.
  Both kinds of diagnostic survive when they coexist.
- Otherwise patient unknown: `potential`, matched/applicable score.
- Otherwise: `eligible`.

## Native/package consumers and ordering

`ExactMatcher(evaluation=session)` chooses conservative candidates and returns an
`EvaluatedTrials` sequence, **not a QuerySet**; its normal no-session interface is
unchanged. Per-trial backend status, missing attributes and MCL diagnostics reuse
that session. Injected-mode hosts must use the versioned result/sequence rather
than attempting SQL filtering after finalization.

Native list/search/count apply study/geography/visibility and DRF text search to
the ORM before materialization. They then evaluate the full bounded scope,
select the final mode, sort and only then count/slice. Retrieve retains normal
PK lookup and object permission checks and reports clinical violations too.
Graph uses that same finalized scope before its `n` limit. Null sort values are
last, with deterministic ascending-ID ties; final clinical score is never
invented for system-unavailable rows.

System-incomplete rows remain visible even in eligible/potential mode, with
explicit status and `matchingCounts` split by outcome; they cannot be silently
dropped or counted as proven eligible. Saved/all modes retain known violations
with their actual verdict. Version: `exact-matching-result-1`.

List/detail scores, actionable missing attributes, explanations, native detail
templates and graph consume the same final result. Typed presentation chooses
its branch **before** legacy TrialTemplates/therapy display/explainer calls;
there is no render-then-overwrite clinical pass. Migrated requirements are native
predicates, not CB codes. Patient values without display bindings are left null,
not reverse-translated or guessed. The list keeps `stage=null` until a native
display binding is supplied, rather than reading the legacy stage column;
`closestLocationGeoPoint` still comes from the sorted location geometry.
Both templates retain all system diagnostic
rows, bypassing legacy empty/select-option suppression. Computed verdicts stay
read-only; source/subform metadata resolves by patient attribute first and
trial-field config second. System failures are never `attributesToFillIn` or the
graph's patient-missing bucket.

## Scope that remains open

This continuation does **not** complete #661 or validate live/CB clients:

- Actual trusted resolver and physical evidence/publication export (#662,
  PROMOP #2124, CB #5616); local shape validity is not certification.
- CB host call-site/presentation integration and a deliberate reviewed immutable
  dependency update. CB-owned shared detail bodies have no supplied-presentation
  seam yet; this native additive branch does not secretly modify them downstream.
- Native typed MCL diagnostics currently expose grouped field outcomes, not a
  complete human-facing per-source-rule explanation UI.
- Review/finalization of typed transport/source-edit display with consumers;
  no clinical answer/label mapping is inferred for absent display bindings.
- Clinical decisions (CB #5352/#5353/#5354/#5343), approved mappings and actual
  patient-side support remain explicit unsupported/unavailable where necessary.
- Cross-host real entry-point parity, catalog query plans, performance budgets
  and target-environment shadow evidence (#663). Default work bounds are safety
  limits, not measured rollout service-level objectives.

Synthetic request tests prove plumbing, grouping, before-page evaluation,
query-count stability and error behavior only. No patient/reference repair,
backfill, package pin update, flag activation, deployment or merge is authorized.
