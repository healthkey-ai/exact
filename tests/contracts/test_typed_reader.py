"""Synthetic OFFLINE behavior only: no clinical approval, readiness or host wiring."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from exact_matching.contracts import ContractError
from exact_matching.contracts.inventory import CRITERIA, MCL_GROUP
from exact_matching.typed_reader import ClaimsEvaluation, FIELD_READERS
from .fixtures import (comparison, concept, context, omop_fact, patient, question,
                       record_fact, trial)

pytestmark = pytest.mark.django_db


def evidence(field, facts=(), *, state='present', completeness='complete'):
    document = patient(field)
    document.update(facts=deepcopy(list(facts)), current=[fact['evidence_id'] for fact in facts],
                    state=state if facts else 'unanswered', completeness=completeness)
    return document


def physical(question, value, *, identity='answer', row='1', column='value_as_string'):
    fact = omop_fact(identity=identity, row=row)
    fact.update(table=question['omop_table'], concept_id=question['concept_id'],
                source_value=question['source_value'])
    fact['answer'].update(value_as_string=None, unit_source_value=question['unit'])
    fact['answer'][column] = value
    return fact


def evaluate(predicate, document):
    return ClaimsEvaluation(context(), [document]).evaluate(predicate)


@pytest.mark.parametrize('spec', CRITERIA, ids=lambda spec: spec.field)
def test_all_55_dispatch_unconstrained_blocked_and_missing_evidence(spec):
    assert len(FIELD_READERS) == 55
    predicate = trial(spec.field)
    assert ClaimsEvaluation(context(), []).evaluate(predicate).status == 'unavailable'
    predicate.update(state='unconstrained', native=[])
    assert ClaimsEvaluation(context(), []).evaluate(predicate).status == 'unconstrained'
    predicate.update(state='blocked', native=None, reason='missing_mapping')
    assert ClaimsEvaluation(context(), []).evaluate(predicate).reasons == ('missing_mapping',)


@pytest.mark.parametrize('spec', CRITERIA, ids=lambda spec: spec.field)
def test_all_55_do_not_guess_binding_for_pending_record_value(spec):
    result = evaluate(trial(spec.field), patient(spec.field))
    assert result.status == 'unsupported'
    assert result.reasons == ('record_operand_binding_missing',)


@pytest.mark.parametrize('availability', ['unavailable', 'unsupported', 'missing_mapping'])
def test_system_availability_is_not_a_patient_question(availability):
    document = patient()
    document.update(availability=availability, issues=['synthetic-system-issue'])
    result = evaluate(trial(document['field']), document)
    assert result.status == ('unsupported' if availability == 'unsupported' else 'unavailable')


@pytest.mark.parametrize('matches', [True, False])
@pytest.mark.parametrize('field', ['her2_statuses_required', 'tumor_stages_excluded'])
def test_exact_physical_string_answer_and_polarity(matches, field):
    predicate = trial(field)
    term = predicate['native'][0]['terms'][0]
    fact = physical(term['question'], term['answer']['value'] if matches else 'Different Literal')
    result = evaluate(predicate, evidence(field, [fact]))
    expected = matches != field.endswith('_excluded')
    assert result.status == ('satisfied' if expected else 'violated')


@pytest.mark.parametrize('change', ['source_value', 'concept_id', 'table'])
def test_wrong_physical_binding_does_not_match(change):
    predicate = trial('her2_statuses_required')
    term = predicate['native'][0]['terms'][0]
    fact = physical(term['question'], term['answer']['value'])
    fact[change] = {'source_value': 'wrong', 'concept_id': '999', 'table': 'observation'}[change]
    result = evaluate(predicate, evidence(predicate['field'], [fact]))
    assert result.status == 'patient_unknown'


def test_answer_column_and_unit_conflicts_are_system_failures():
    predicate = trial('her2_statuses_required')
    term = predicate['native'][0]['terms'][0]
    fact = physical(term['question'], term['answer']['value'])
    fact['answer']['value_as_number'] = 0
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'unavailable'
    fact['answer']['value_as_number'] = None
    fact['answer']['unit_source_value'] = 'unapproved unit'
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'unavailable'


def test_pending_clear_never_falls_back_to_superseded_physical_fact():
    predicate = trial('her2_statuses_required')
    term = predicate['native'][0]['terms'][0]
    old = physical(term['question'], term['answer']['value'])
    clear = record_fact({'kind': 'clear', 'value': None}, state='cleared')
    document = evidence(predicate['field'], [clear], state='cleared')
    document['facts'].append(old)
    document['superseded'] = [old['evidence_id']]
    assert evaluate(predicate, document).status == 'patient_unknown'


@pytest.mark.parametrize('state', ['present', 'asserted_none', 'negative'])
def test_partial_or_complete_unrelated_facts_do_not_prove_absence(state):
    predicate = trial('cytogenic_markers_required')
    predicate['native'][0]['terms'] = [{'kind': 'assertion', 'concept': concept(),
                                      'state': 'encoded_by_concept'}]
    fact = omop_fact()
    fact.update(table='condition_occurrence', concept_id='999', state=state)
    del fact['answer']
    document = evidence(predicate['field'], [fact], state=state, completeness='partial')
    assert evaluate(predicate, document).status == 'patient_unknown'
    document['completeness'] = 'complete'
    assert evaluate(predicate, document).status == 'patient_unknown'


def test_encoded_negative_assertion_is_not_overlaid_with_positive_status():
    predicate = trial('her2_statuses_required')
    predicate['native'][0]['terms'] = [{'kind': 'assertion', 'concept': concept(),
                                      'state': 'encoded_by_concept'}]
    predicate['answer_bindings'] = []
    fact = omop_fact()
    fact.update(table='condition_occurrence', concept_id=concept()['concept_id'])
    del fact['answer']
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'satisfied'


def linked(subject, question, value, *, identity='component', row='2'):
    fact = physical(question, value, identity=identity, row=row)
    fact['parent'] = {
        'table': 'measurement', 'row_id': subject['row_id'], 'person_key': subject['person_key'],
        'event_field_name': 'measurement.measurement_id',
        'event_field_concept': {**concept(), 'vocabulary_id': 'CDM',
                                'concept_code': 'measurement.measurement_id'},
    }
    return fact


def genomic_case(field='cytogenic_markers_required', status='present'):
    predicate = trial(field)
    term = predicate['native'][0]['terms'][0]
    subject = omop_fact(identity='subject', row='1')
    subject['concept_id'] = term['feature']['concept_id']
    # Match the physical subject table specified by the native concept domain.
    subject['table'] = 'observation'
    parent = omop_fact(identity='parent', row='3')
    subject['parent'] = linked(parent, question(), '')['parent']
    facts = [parent, subject]
    if term['kind'] == 'finding':
        facts.append(linked(parent, term['status']['question'], status))
    elif 'qualifier' in term:
        bound = term['qualifier']
        facts.append(linked(parent, bound['question'], bound['answer']['value']))
    return predicate, evidence(field, facts)


@pytest.mark.parametrize('status,expected', [('present', 'satisfied'), ('absent', 'violated'),
                                            ('indeterminate', 'patient_unknown'), ('', 'patient_unknown')])
def test_finding_requires_same_parent_explicit_status(status, expected):
    predicate, document = genomic_case(status=status)
    assert evaluate(predicate, document).status == expected


def test_selected_child_cannot_use_superseded_parent():
    predicate, document = genomic_case()
    document['current'].remove('parent')
    document['superseded'].append('parent')
    result = evaluate(predicate, document)
    assert result.status == 'unavailable'
    assert result.reasons == ('selected_parent_missing',)


@pytest.mark.parametrize('field', ['mutation_genes_required', 'mutation_variants_required',
                                  'mutation_origins_required', 'mutation_interpretations_required'])
def test_genomic_axes_use_independent_exists_and_bound_qualifiers(field):
    predicate, document = genomic_case(field)
    assert evaluate(predicate, document).status == 'satisfied'
    if field.endswith(('origins_required', 'interpretations_required')):
        # Move qualifier to another current finding: no accidental cross-finding join.
        other = omop_fact(identity='other', row='4')
        document['facts'].append(other)
        document['current'].append('other')
        document['facts'][-2]['parent']['row_id'] = '4'
        assert evaluate(predicate, document).status == 'patient_unknown'


@pytest.mark.parametrize('value,expected', [(0, 'violated'), (5, 'satisfied'), (4.999, 'violated')])
def test_rule_comparison_boundary_and_zero(value, expected):
    predicate = trial('bulky_disease_criteria_required')
    operand = predicate['native'][0]['terms'][0]['predicate']['question']
    fact = physical(operand, value, column='value_as_number')
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == expected


def mcl_documents():
    return [trial(field) for field in MCL_GROUP]


def mcl_evidence(values):
    return [evidence(field, [physical(comparison()['question'], value, column='value_as_number')])
            for field, value in zip(MCL_GROUP, values)]


@pytest.mark.parametrize('values,expected', [([5, 0, 0], 'satisfied'), ([0, 0, 5], 'satisfied'),
                                            ([5, 5, 5], 'violated'), ([0, 0, 0], 'violated')])
def test_mcl_required_or_sufficient_with_exclusion_precedence(values, expected):
    session = ClaimsEvaluation(context(), mcl_evidence(values))
    assert session.mcl_group(mcl_documents()).status == expected


def test_source_rule_identity_not_concept_identity_controls_count():
    predicates = mcl_documents()
    required = predicates[0]
    required['source_companions']['high_risk_mcl_criteria_min_count'] = 2
    envelope = required['native'][0]
    envelope['minimum'] = 2
    second = deepcopy(envelope['terms'][0])
    second['rule_identity'] += '-distinct-source-rule'
    envelope['terms'].append(second)
    doc = mcl_evidence([5, 0, 0])[0]
    assert evaluate(required, doc).status == 'satisfied'
    second['rule_identity'] = envelope['terms'][0]['rule_identity']
    with pytest.raises(ContractError, match='duplicate_rule_identity'):
        evaluate(required, doc)


def test_mcl_raw_zero_keeps_existing_emitted_minimum_one():
    predicates = mcl_documents()
    for document in predicates:
        document['source_companions']['high_risk_mcl_criteria_min_count'] = 0
    session = ClaimsEvaluation(context(), mcl_evidence([0, 0, 0]))
    assert session.mcl_group(predicates).status == 'violated'


def test_unsupported_or_member_cannot_disappear_when_another_is_true():
    predicate = trial('high_risk_mcl_criteria_required')
    other = deepcopy(predicate['native'][0]['terms'][0])
    other['rule_identity'] += '-unsupported'
    other['predicate'] = {'kind': 'assertion', 'concept': concept('Unbound Domain'),
                          'state': 'encoded_by_concept'}
    predicate['native'][0]['terms'].append(other)
    assert evaluate(predicate, mcl_evidence([5, 0, 0])[0]).status == 'unsupported'


def test_corruption_is_explicit_and_does_not_cache_partial_results():
    predicate = trial('her2_statuses_required')
    session = ClaimsEvaluation(context(), [])
    predicate['native'][0]['schema'] = 'unrecognized'
    with pytest.raises(ContractError, match='schema_TrialPredicate'):
        session.evaluate(predicate)
    assert not session._cache


def test_snapshot_and_results_are_immutable_and_cache_uses_full_predicate():
    predicate = trial('her2_statuses_required')
    term = predicate['native'][0]['terms'][0]
    document = evidence(predicate['field'], [physical(term['question'], term['answer']['value'])])
    session = ClaimsEvaluation(context(), [document])
    document['facts'][0]['answer']['value_as_string'] = 'caller mutation'
    result = session.evaluate(predicate)
    assert result.status == 'satisfied'
    assert session.evaluate(deepcopy(predicate)) is result
    with pytest.raises(FrozenInstanceError):
        result.status = 'violated'
    predicate['dependencies'][0]['source_sha256'] = '9' * 64
    assert session.evaluate(predicate) is not result


def test_budget_failure_is_explicit_not_a_truncated_result():
    session = ClaimsEvaluation(context(), [], max_evaluations=1)
    session.evaluate(trial('her2_statuses_required'))
    with pytest.raises(ContractError, match='evaluation_budget'):
        session.evaluate(trial('ethnicity_required'))


def test_person_scope_context_and_duplicate_field_rejection():
    first = patient('her2_statuses_required')
    second = patient('hr_statuses_required')
    second['scope']['disease_key'] = 'another-scope'
    with pytest.raises(ContractError, match='evidence_snapshot_identity'):
        ClaimsEvaluation(context(), [first, second])
    with pytest.raises(ContractError, match='duplicate_evidence_field'):
        ClaimsEvaluation(context(), [first, first])
    first['context_id'] = '0' * 64
    with pytest.raises(ContractError, match='context_identity'):
        ClaimsEvaluation(context(), [first])


def capability(value, *, field='english_speak', identity='language', language='990000001',
               skill='990000002'):
    fact = record_fact(identity=identity)
    for key in ('projection', 'value'):
        del fact[key]
    fact.update(kind='language_capability', field_name=field, language_concept_id=language,
                skill_concept_id=skill, value=value,
                state='unanswered' if value is None else 'present' if value else 'negative')
    return fact


@pytest.mark.parametrize('value,expected', [(True, 'satisfied'), (False, 'violated'),
                                          (None, 'patient_unknown')])
def test_language_false_and_never_asked_are_distinct(value, expected):
    fact = capability(value)
    predicate = trial('languages_skills_required')
    document = evidence(predicate['field'], [fact], state=fact['state'])
    assert evaluate(predicate, document).status == expected


@pytest.mark.parametrize('field', ['english_read', 'english_understand'])
def test_transitional_language_builder_only_holds_speak_write(field):
    fact = capability(True, field=field)
    predicate = trial('languages_skills_required')
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'violated'


def test_language_mixed_skills_and_conflicting_same_pair():
    predicate = trial('languages_skills_required')
    yes = capability(True)
    no = capability(False, field='english_write', identity='other-skill', skill='999')
    document = evidence(predicate['field'], [yes, no], state='mixed')
    assert evaluate(predicate, document).status == 'satisfied'
    no['skill_concept_id'] = yes['skill_concept_id']
    document = evidence(predicate['field'], [yes, no], state='conflict')
    assert evaluate(predicate, document).status == 'patient_unknown'


def test_language_any_unasked_required_language_keeps_unknown_unless_overlap():
    predicate = trial('languages_skills_required')
    predicate['native'].append('999:990000002')
    fact = capability(False)
    assert evaluate(predicate, evidence(predicate['field'], [fact], state='negative')).status == 'patient_unknown'
    fact = capability(True)
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'satisfied'


@pytest.mark.parametrize('spec', CRITERIA, ids=lambda spec: spec.field)
def test_all_55_empty_patient_routes_do_not_become_a_negative_answer(spec):
    result = evaluate(trial(spec.field), evidence(spec.field))
    expected = ('unsupported' if spec.operator in ('concept_set', 'temporal_sct', 'medication')
                else 'patient_unknown')
    assert result.status == expected


@pytest.mark.parametrize('spec', [spec for spec in CRITERIA if spec.operator == 'coded_result'],
                         ids=lambda spec: spec.field)
def test_every_coded_result_dispatch_reads_bound_answer(spec):
    predicate = trial(spec.field)
    term = predicate['native'][0]['terms'][0]
    fact = physical(term['question'], term['answer']['value'])
    result = evaluate(predicate, evidence(spec.field, [fact]))
    assert result.status == ('violated' if spec.role == 'excluded' else 'satisfied')


def test_concept_answer_keeps_writer_display_but_rejects_zero_mapping():
    from .fixtures import binding_claims, bound_answer
    predicate = trial('her2_statuses_required')
    bound = bound_answer('concept')
    predicate['native'][0]['terms'] = [{'kind': 'coded_result',
                                      'association': 'same_observation', **bound}]
    predicate['answer_bindings'] = [binding_claims(bound)]
    fact = physical(bound['question'], bound['answer']['concept']['concept_id'],
                    column='value_as_concept_id')
    fact['answer']['value_as_string'] = 'Exact Synthetic Display Title'
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'satisfied'
    fact['answer']['value_as_concept_id'] = '0'
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'unavailable'


@pytest.mark.parametrize('reverse', [True, False])
@pytest.mark.parametrize('unmapped_id', ['0', None])
def test_unmapped_concept_answer_cannot_be_hidden_by_another_selected_answer(reverse, unmapped_id):
    from .fixtures import binding_claims, bound_answer
    predicate = trial('her2_statuses_required')
    bound = bound_answer('concept')
    predicate['native'][0]['terms'] = [{'kind': 'coded_result',
                                      'association': 'same_observation', **bound}]
    predicate['answer_bindings'] = [binding_claims(bound)]
    mapped = physical(bound['question'], bound['answer']['concept']['concept_id'],
                      column='value_as_concept_id')
    unmapped = physical(bound['question'], unmapped_id, column='value_as_concept_id', identity='unmapped', row='2')
    unmapped['answer']['value_as_string'] = 'Exact Synthetic Display Title'
    facts = [mapped, unmapped]
    if reverse:
        facts.reverse()
    result = evaluate(predicate, evidence(predicate['field'], facts))
    assert result.status == 'unavailable'
    assert result.reasons == ('physical_answer_unmapped',)


def test_multiselect_matches_declared_exact_literal_not_substring():
    from .fixtures import binding_claims
    predicate = trial('staging_modalities_required')
    term = predicate['native'][0]['terms'][0]
    term['answer']['match'] = 'comma_joined_member'
    predicate['answer_bindings'] = [binding_claims(term, multiple=True)]
    title = term['answer']['value']
    fact = physical(term['question'], f'Other, {title}')
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'satisfied'
    fact['answer']['value_as_string'] += ' extra'
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'violated'


@pytest.mark.parametrize('operator,value,expected', [('gt', 5, 'violated'), ('gte', 5, 'satisfied'),
                                                   ('lt', 5, 'violated'), ('lte', 5, 'satisfied')])
def test_every_numeric_operator_at_equality(operator, value, expected):
    predicate = trial('bulky_disease_criteria_required')
    rule = predicate['native'][0]['terms'][0]['predicate']
    rule['operator'] = operator
    fact = physical(rule['question'], value, column='value_as_number')
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == expected


def test_guard_unknown_cannot_be_short_circuited_by_false_comparison():
    predicate = trial('high_risk_mcl_criteria_required')
    rule = predicate['native'][0]['terms'][0]
    operand = comparison()['question']
    rule['predicate'] = {'kind': 'all_of', 'terms': [comparison()],
                         'unknown_if_missing': [operand], 'unknown_if_nonpositive': [operand]}
    fact = physical(operand, 0, column='value_as_number')
    assert evaluate(predicate, evidence(predicate['field'], [fact])).status == 'patient_unknown'


def test_mcl_count_bounds_with_unknown_and_known_exclusion_diagnostic():
    predicates = mcl_documents()
    documents = mcl_evidence([5, 0, 0])
    documents[0] = evidence(MCL_GROUP[0])
    assert ClaimsEvaluation(context(), documents).mcl_group(predicates).status == 'patient_unknown'
    documents[2] = mcl_evidence([5, 0, 5])[2]
    assert ClaimsEvaluation(context(), documents).mcl_group(predicates).status == 'satisfied'
    documents[1] = mcl_evidence([5, 5, 0])[1]
    documents[0].update(availability='unavailable', issues=['synthetic'])
    result = ClaimsEvaluation(context(), documents).mcl_group(predicates)
    assert result.status == 'unavailable'
    assert 'known_exclusion_violation' in result.reasons


def test_mcl_sibling_closure_and_companion_consistency():
    session = ClaimsEvaluation(context(), [])
    predicates = mcl_documents()
    with pytest.raises(ContractError, match='mcl_group_closure'):
        session.mcl_group(predicates[:2])
    predicates[0]['source_companions']['high_risk_mcl_criteria_min_count'] = 0
    with pytest.raises(ContractError, match='mcl_companion_identity'):
        session.mcl_group(predicates)
    predicates = mcl_documents()
    predicates[0]['dependencies'][0]['source_sha256'] = '8' * 64
    with pytest.raises(ContractError, match='mcl_dependency_identity'):
        session.mcl_group(predicates)


def test_sct_zero_elapsed_requirement_stays_explicit_unsupported():
    predicate = trial('stem_cell_transplant_history_excluded')
    predicate['source_companions']['day_since_stem_cell_transplant_required'] = 0
    predicate['native'][0].update(terms=[], independent_requirements={
        'event': concept('Procedure'), 'history_required': None,
        'minimum_elapsed_days': 0, 'elapsed_operator': 'gte',
        'temporal_anchor': 'evaluation_date', 'polarity': 'required',
        'combination': 'and_with_exclusion_verdict', 'unknown_is_negative': False,
    })
    result = evaluate(predicate, evidence(predicate['field']))
    assert result.status == 'unsupported'
    assert 'sct_event_date_binding_missing' in result.reasons


def test_reader_never_imports_host_models_or_changes_settings():
    import ast
    from pathlib import Path
    source = Path(__file__).resolve().parents[2] / 'matcher_app/exact_matching/typed_reader.py'
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or '').startswith(('django', 'trials', 'omop_core'))
        if isinstance(node, ast.Import):
            assert all(not item.name.startswith(('django', 'trials', 'omop_core')) for item in node.names)
