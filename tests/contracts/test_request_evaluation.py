"""Synthetic orchestration receipts, not authenticated publication or readiness."""
from copy import deepcopy

import pytest

from exact_matching.contracts import ContractError
from exact_matching.contracts.inventory import CRITERIA, MCL_GROUP
from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.request_evaluation import EvaluatedTrials, RequestEvaluation, TrialPredicates
from tests.factories import TrialFactory
from trials.services.patient_info.patient_info import PatientInfo
from .fixtures import context, trial, concept
from .test_typed_reader import evidence, physical, genomic_case

pytestmark = pytest.mark.django_db


def predicates(**overrides):
    documents = []
    for spec in CRITERIA:
        document = trial(spec.field)
        document.update(state='unconstrained', native=[], answer_bindings=[])
        documents.append(overrides.get(spec.field, document))
    return tuple(documents)


def session(documents=None, *, patient=None, evidence_documents=(), max_trials=200):
    patient = patient or PatientInfo(disease='breast cancer')
    calls = []

    def load(identities):
        calls.append(identities)
        return {item.trial_id: TrialPredicates(item.row_sha256,
                    documents[item.trial_id] if documents else predicates()) for item in identities}

    return RequestEvaluation(context(), list(evidence_documents), patient, load, max_trials=max_trials), calls


def test_unconstrained_score_matches_exact_python_reference_and_batch_is_once():
    row = TrialFactory(disease='breast cancer')
    patient = PatientInfo(disease='breast cancer')
    evaluation, calls = session(patient=patient)
    result, = evaluation.evaluate([row])
    assert (result.score, result.status) == UserToTrialAttrMatcher(row, patient).match_score_and_status()
    assert len(result.criteria) == 55
    assert evaluation.evaluate([row])[0] is result
    assert len(calls) == 1


def test_migrated_attrs_never_read_legacy_answers_even_for_system_gaps(monkeypatch):
    row = TrialFactory(disease='breast cancer')
    document = trial('her2_statuses_required')
    evaluation, _ = session({row.pk: predicates(her2_statuses_required=document)})
    original = UserToTrialAttrMatcher.attr_match_status

    def scalar_only(self, attr):
        assert attr not in evaluation._groups
        return original(self, attr)

    monkeypatch.setattr(UserToTrialAttrMatcher, 'attr_match_status', scalar_only)
    result, = evaluation.evaluate([row])
    assert result.status == 'unavailable' and result.score is None
    assert 'her2_status' not in result.missing_attributes
    assert result.system_issues[0].attribute == 'her2_status'


def test_unknown_scalar_and_supported_typed_answer_share_one_score_denominator():
    row = TrialFactory(disease='breast cancer', age_low_limit=18)
    document = trial('her2_statuses_required')
    bound = document['native'][0]['terms'][0]
    facts = [physical(bound['question'], bound['answer']['value'])]
    evaluation, _ = session({row.pk: predicates(her2_statuses_required=document)},
                            evidence_documents=[evidence(document['field'], facts)])
    result, = evaluation.evaluate([row])
    assert result.status == 'potential'
    assert result.missing_attributes == ('patient_age',)
    assert result.score == int(100 * (len(result.attributes) - 1) / len(result.attributes))


def test_known_scalar_violation_preserves_system_issues_and_scores_zero():
    row = TrialFactory(disease='breast cancer', age_low_limit=50)
    evaluation, _ = session({row.pk: predicates(her2_statuses_required=trial('her2_statuses_required'))},
                            patient=PatientInfo(disease='breast cancer', patient_age=20))
    result, = evaluation.evaluate([row])
    assert (result.status, result.score) == ('not_eligible', 0)
    assert result.system_issues


def test_mcl_required_violation_is_not_final_when_sufficient_branch_passes():
    row = TrialFactory(disease='mantle cell lymphoma')
    docs = {name: trial(name) for name in MCL_GROUP}
    facts = []
    for name, document in docs.items():
        # Each independent rule binds its own physical source; required fails,
        # sufficient passes, exclusion does not apply.
        if name == MCL_GROUP[1]:
            document.update(state='unconstrained', native=[])
            continue
        question = document['native'][0]['terms'][0]['predicate']['question']
        question['source_value'] = name
        facts.append(evidence(name, [physical(question, 0 if name == MCL_GROUP[0] else 10,
                                               column='value_as_number')]))
    evaluation, _ = session({row.pk: predicates(**docs)}, patient=PatientInfo(disease='mantle cell lymphoma'),
                            evidence_documents=facts)
    result, = evaluation.evaluate([row])
    group = next(item for item in result.attributes if item.attribute == 'high_risk_mcl_criteria')
    assert group.status == 'satisfied' and not group.known_violation
    assert result.status == 'eligible'


def test_sct_unrestricted_day_does_not_inherit_history_system_gap():
    row = TrialFactory()
    document = trial('stem_cell_transplant_history_excluded')
    evaluation, _ = session({row.pk: predicates(stem_cell_transplant_history_excluded=document)},
                            patient=PatientInfo(disease='multiple myeloma'))
    result, = evaluation.evaluate([row])
    by_attr = {item.attribute: item for item in result.attributes}
    assert by_attr['stem_cell_transplant_history'].status == 'unavailable'
    assert by_attr['day_since_stem_cell_transplant'].status == 'satisfied'


def test_sct_elapsed_zero_is_constrained_not_a_blank_companion():
    row = TrialFactory()
    document = trial('stem_cell_transplant_history_excluded')
    document['source_companions']['day_since_stem_cell_transplant_required'] = 0
    # The contract forbids claiming no constraint when an independent requirement
    # is missing from the native envelope. Corrupt data does not become a score.
    evaluation, _ = session({row.pk: predicates(stem_cell_transplant_history_excluded=document)},
                            patient=PatientInfo(disease='multiple myeloma'))
    with pytest.raises(ContractError):
        evaluation.evaluate([row])


def test_valid_zero_elapsed_and_unrestricted_history_have_separate_contributions():
    row = TrialFactory()
    document = trial('stem_cell_transplant_history_excluded')
    document['source_companions']['day_since_stem_cell_transplant_required'] = 0
    document['native'][0].update(terms=[], independent_requirements={
        'event': concept('Procedure'), 'history_required': None, 'minimum_elapsed_days': 0,
        'elapsed_operator': 'gte', 'temporal_anchor': 'evaluation_date', 'polarity': 'required',
        'combination': 'and_with_exclusion_verdict', 'unknown_is_negative': False})
    evaluation, _ = session({row.pk: predicates(stem_cell_transplant_history_excluded=document)},
                            patient=PatientInfo(disease='multiple myeloma'),
                            evidence_documents=[evidence(document['field'])])
    by_attr = {item.attribute: item for item in evaluation.evaluate([row])[0].attributes}
    assert by_attr['stem_cell_transplant_history'].status == 'satisfied'
    assert by_attr['day_since_stem_cell_transplant'].status == 'unsupported'


@pytest.mark.parametrize('spec', CRITERIA, ids=lambda spec: spec.field)
def test_every_field_has_explicit_request_dispatch_and_applicability(spec):
    diseases = {'MM': 'multiple myeloma', 'BC': 'breast cancer', 'CLL': 'chronic lymphocytic leukemia',
                'FL': 'follicular lymphoma', 'MCL': 'mantle cell lymphoma', 'all': 'breast cancer'}
    disease = diseases[spec.applicability[0]]
    row = TrialFactory(disease=disease)
    evaluation, _ = session({row.pk: predicates(**{spec.field: trial(spec.field)})},
                            patient=PatientInfo(disease=disease))
    result, = evaluation.evaluate([row])
    assert len(result.criteria) == 55
    assert result.status == 'unavailable' and result.score is None
    assert any(spec.field in item.fields for item in result.system_issues)


def test_mcl_known_exclusion_remains_conclusive_alongside_system_gap():
    row = TrialFactory(disease='mantle cell lymphoma')
    required = trial(MCL_GROUP[0])
    excluded = trial(MCL_GROUP[1])
    question = excluded['native'][0]['terms'][0]['predicate']['question']
    evaluation, _ = session({row.pk: predicates(**{required['field']: required, excluded['field']: excluded})},
                            patient=PatientInfo(disease='mantle cell lymphoma'), evidence_documents=[
                                evidence(excluded['field'], [physical(question, 10, column='value_as_number')])])
    result, = evaluation.evaluate([row])
    assert (result.status, result.score) == ('not_eligible', 0)
    assert any('known_exclusion_violation' in item.reasons for item in result.system_issues)


def test_existing_patient_record_surface_is_reused_not_replaced():
    from exact_matching.patient_record import PatientRecordSurface
    from tests.matching.test_patient_record_surface import Record

    class Surface(PatientRecordSurface):
        _meta = PatientInfo._meta
        DERIVED_FIELDS_IN_SAVE = ()

    surface = Surface(Record(disease='breast cancer', gender='M'))
    row = TrialFactory(disease='breast cancer', gender='F')
    evaluation, _ = session(patient=surface)
    assert isinstance(evaluation.patient_info, Surface)
    assert evaluation.evaluate([row])[0].status == 'not_eligible'


def test_sliced_or_deferred_querysets_do_not_disguise_an_incomplete_catalog():
    from trials.models import Trial
    TrialFactory()
    evaluation, calls = session()
    with pytest.raises(ContractError, match='sliced_candidates'):
        evaluation.evaluate(Trial.objects.all()[:1])
    with pytest.raises(ContractError, match='deferred_trial_snapshot'):
        evaluation.evaluate(Trial.objects.only('id'))
    assert not calls


def test_all_55_validated_even_when_disease_does_not_apply():
    row = TrialFactory()
    document = trial('her2_statuses_required')
    document['context_id'] = 'wrong-context'
    evaluation, _ = session({row.pk: predicates(her2_statuses_required=document)})
    with pytest.raises(ContractError):
        evaluation.evaluate([row])


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown', 'wrong_binding', 'dependency',
                                     'list_field', 'object_field'])
def test_invalid_batch_fails_whole_operation(mutation):
    row = TrialFactory()
    evaluation, _ = session()
    original = evaluation._batch_reader

    def bad_batch(identities):
        batch = original(identities)
        supplied = batch[row.pk]
        docs = list(deepcopy(supplied.predicates))
        if mutation == 'missing':
            docs.pop()
        elif mutation == 'duplicate':
            docs[-1] = deepcopy(docs[0])
        elif mutation == 'unknown':
            docs[-1]['field'] = 'not_a_criterion'
        elif mutation in ('list_field', 'object_field'):
            docs[-1]['field'] = [] if mutation == 'list_field' else {}
        elif mutation == 'dependency':
            # Genomic sibling dependencies must agree across the batch.
            doc = next(item for item in docs if item['field'] == 'mutation_genes_required')
            doc['dependencies'][0]['source_sha256'] = '9' * 64
        batch[row.pk] = TrialPredicates('wrong' if mutation == 'wrong_binding' else supplied.row_sha256, tuple(docs))
        return batch

    evaluation._batch_reader = bad_batch
    with pytest.raises(ContractError):
        evaluation.evaluate([row])


def test_changed_scalar_row_rejected_instead_of_reusing_predicate_only_cache():
    row = TrialFactory()
    evaluation, _ = session()
    evaluation.evaluate([row])
    row.age_low_limit = 50
    with pytest.raises(ContractError, match='trial_snapshot_changed'):
        evaluation.evaluate([row])


def test_patient_input_mutation_does_not_change_request_snapshot():
    row = TrialFactory(disease='breast cancer', age_low_limit=50)
    patient = PatientInfo(disease='breast cancer', patient_age=20)
    evaluation, _ = session(patient=patient)
    patient.patient_age = 70
    assert evaluation.evaluate([row])[0].status == 'not_eligible'


def test_budget_rejects_before_producer_is_called_and_never_returns_partial_page():
    rows = [TrialFactory(), TrialFactory()]
    evaluation, calls = session(max_trials=1)
    with pytest.raises(ContractError, match='candidate_budget'):
        EvaluatedTrials(rows, evaluation)
    assert not calls


def test_final_filter_and_null_last_sort_are_before_slicing():
    rows = [TrialFactory(disease='breast cancer', age_low_limit=50) for _ in range(3)]
    rows.append(TrialFactory(disease='breast cancer'))
    docs = {row.pk: predicates() for row in rows}
    docs[rows[1].pk] = predicates(her2_statuses_required=trial('her2_statuses_required'))
    # First candidates reject, final one is eligible. Scalar violation on row[1]
    # is conclusive even with its unavailable HER2 evidence.
    evaluation, _ = session(docs, patient=PatientInfo(disease='breast cancer', patient_age=20))
    finalized = EvaluatedTrials(rows, evaluation, search_type='eligible')
    assert finalized.count() == 1 and finalized[0].pk == rows[-1].pk
    all_rows = EvaluatedTrials(rows, evaluation, search_type='all')
    ordered = all_rows.ordered(lambda row: evaluation.result_for(row.pk).score, reverse=True)
    assert ordered[0].pk == rows[-1].pk


def test_system_gap_not_silently_hidden_by_eligible_filter():
    row = TrialFactory(disease='breast cancer')
    evaluation, _ = session({row.pk: predicates(her2_statuses_required=trial('her2_statuses_required'))})
    finalized = EvaluatedTrials([row], evaluation, search_type='eligible')
    assert finalized.count() == 1
    assert evaluation.result_for(row.pk).status == 'unavailable'
    assert row.match_score is None
