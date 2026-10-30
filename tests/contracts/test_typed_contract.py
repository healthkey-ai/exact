"""No clinical verdicts here: only schema, identity and association contracts."""
import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from exact_matching.contracts import ContractError, MatchingContractContext, schema_for, validate
from exact_matching.contracts.inventory import CRITERIA, MCL_GROUP
from exact_matching.patient_info.configs import TRIAL_ATTRS_JSON_AS_A_LIST
from .fixtures import (binding_claims, bound_answer, comparison, concept, context, context_claims,
                       omop_fact, patient, question, record_fact, trial)

pytestmark = pytest.mark.django_db  # Root autouse reference loaders use local pytest DB.


@pytest.mark.parametrize('name', ['TrialPredicate', 'PatientEvidence', 'Context'])
def test_schema_is_draft_202012_and_only_has_local_references(name):
    schema = schema_for(name)
    Draft202012Validator.check_schema(schema)
    encoded = json.dumps(schema)
    assert 'https://json-schema.org/draft/2020-12/schema' in encoded
    assert all(value.startswith('#/') for value in _refs(schema))


def _refs(node):
    if isinstance(node, dict):
        if '$ref' in node:
            yield node['$ref']
        for value in node.values():
            yield from _refs(value)
    if isinstance(node, list):
        for value in node:
            yield from _refs(value)


def test_inventory_is_exactly_all_55_source_lists_not_implementation_waves():
    expected = set(TRIAL_ATTRS_JSON_AS_A_LIST) - {
        'omop_languages_skills_required', 'myeloma_related_disorders_excluded',
    }
    assert len(CRITERIA) == len({item.field for item in CRITERIA}) == len(expected) == 55
    assert {item.field for item in CRITERIA} == expected
    for item in CRITERIA:
        assert item.source_operands and item.evidence_source.physical_source
        assert item.evidence_source.unresolved_gates
        assert item.applicability and item.field in item.dependency_closure


def test_inventory_applicability_matches_frozen_existing_config_without_importing_hosts():
    tree = ast.parse(Path(__file__).resolve().parents[2].joinpath(
        'matcher_app/exact_matching/patient_info/configs.py').read_text())
    mapping = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'USER_TO_TRIAL_ATTRS_MAPPING'
                           for target in node.targets))
    found = {}
    for entry in mapping.values:
        if not isinstance(entry, ast.Dict):
            continue
        values = {ast.literal_eval(key): value for key, value in zip(entry.keys, entry.values)}
        source = values.get('attr')
        if isinstance(source, ast.Name) and source.id == 'TRIAL_GENETIC_MUTATIONS_ATTRS_UNDERSCORED':
            fields = [item.field for item in CRITERIA if item.field.startswith('mutation_')]
        else:
            try:
                fields = ast.literal_eval(source)
            except (ValueError, TypeError):
                continue
        fields = [fields] if isinstance(fields, str) else fields
        diseases = ast.literal_eval(values.get('disease', ast.Constant('all')))
        diseases = (diseases,) if isinstance(diseases, str) else tuple(diseases)
        for field in fields:
            found[field] = diseases
    # Therapy lineage fields are assembled separately in config; do not invent
    # a disease restriction for a future authoring route.
    for item in CRITERIA:
        if item.field in found:
            assert item.applicability == found[item.field], item.field


@pytest.mark.parametrize('spec', CRITERIA, ids=lambda item: item.field)
def test_every_registry_entry_has_synthetic_provenance_labelled_native_fixture(spec):
    document = trial(spec.field)
    validate(document, 'TrialPredicate', context=context())
    validate(patient(spec.field), 'PatientEvidence', context=context())
    assert document['provenance']['assurance'] == 'synthetic'


@pytest.mark.parametrize('spec', CRITERIA, ids=lambda item: item.field)
def test_every_registry_entry_distinguishes_unconstrained_blocked_and_unavailable(spec):
    document = trial(spec.field)
    document.update(native=[], state='unconstrained')
    validate(document, 'TrialPredicate', context=context())
    document.update(native=None, state='blocked', reason='missing_mapping')
    validate(document, 'TrialPredicate', context=context())
    document.update(state='unconstrained')
    with pytest.raises(ContractError):
        validate(document, 'TrialPredicate', context=context())
    evidence = patient(spec.field)
    evidence.update(availability='unsupported', issues=['synthetic-capability-gap'])
    validate(evidence, 'PatientEvidence', context=context())


def test_context_is_deeply_immutable_but_does_not_certify_synthetic_claims():
    claims = context_claims()
    pinned = MatchingContractContext.from_claims(claims)
    identity = pinned.context_id
    claims['provenance']['assurance'] = 'unverified'
    returned = pinned.as_claims()
    returned['mapping_sha256'] = '9' * 64
    assert pinned.context_id == identity
    assert pinned.as_claims()['provenance']['assurance'] == 'synthetic'
    assert not hasattr(pinned, 'ready')
    with pytest.raises(FrozenInstanceError):
        pinned._canonical_claims = '{}'


@pytest.mark.parametrize('key,value', [
    ('registry_sha256', '5' * 64), ('mapping_sha256', '5' * 64),
    ('vocabulary_release', 'different-release'), ('vocabulary_sha256', '5' * 64),
    ('trial_generation', 'different-trials'), ('authoring_generation', 'different-authoring'),
    ('patient_snapshot', 'different-patient-snapshot'), ('evaluation_date', '2026-10-31'),
])
def test_any_identity_change_breaks_request_binding(key, value):
    claims = context_claims()
    claims[key] = value
    changed = MatchingContractContext.from_claims(claims)
    assert context().context_id != changed.context_id
    with pytest.raises(ContractError, match='context_identity'):
        validate(trial('stages'), 'TrialPredicate', context=changed)


@pytest.mark.parametrize('kind,value', [('number', 0), ('boolean', False), ('string', ''), ('collection', [])])
def test_false_zero_empty_collection_and_empty_string_are_not_coerced(kind, value):
    evidence = patient()
    evidence['facts'][0]['value'] = {'kind': kind, 'value': value}
    if kind == 'number':
        evidence['facts'][0]['value']['unit'] = None
    validate(evidence, 'PatientEvidence', context=context())
    assert evidence['facts'][0]['value']['value'] == value
    assert type(evidence['facts'][0]['value']['value']) is type(value)


@pytest.mark.parametrize('state', ['unanswered', 'asserted_none', 'negative', 'indeterminate', 'conflict', 'cleared'])
def test_all_clinical_states_are_explicit_and_not_inferred_from_empty_results(state):
    evidence = patient()
    evidence['state'] = state
    if state == 'unanswered':
        evidence.update(facts=[], current=[])
    elif state == 'conflict':
        other = record_fact({'kind': 'string', 'value': 'different answer'}, identity='conflict-edit')
        evidence['facts'].append(other)
        evidence['current'].append(other['evidence_id'])
    else:
        evidence['facts'][0]['state'] = state
        if state == 'cleared':
            evidence['facts'][0]['value'] = {'kind': 'clear', 'value': None}
    validate(evidence, 'PatientEvidence', context=context())
    if state != 'unanswered':
        evidence.update(facts=[], current=[])
        with pytest.raises(ContractError):
            validate(evidence, 'PatientEvidence', context=context())


def test_pending_clear_supersedes_old_omop_without_fabricating_a_parent_or_refresh():
    evidence = patient()
    evidence['state'] = evidence['facts'][0]['state'] = 'cleared'
    evidence['facts'][0]['value'] = {'kind': 'clear', 'value': None}
    old = omop_fact()
    evidence['facts'].append(old)
    evidence['superseded'] = [old['evidence_id']]
    validate(evidence, 'PatientEvidence', context=context())
    assert evidence['facts'][0]['projection'] == 'pending'
    assert 'parent' not in evidence['facts'][0]
    evidence['current'].append(old['evidence_id'])
    evidence['superseded'] = []
    with pytest.raises(ContractError, match='selected_state'):
        validate(evidence, 'PatientEvidence', context=context())


def linked_genomics():
    evidence = patient('mutation_origins_required')
    parent = omop_fact(identity='parent', row='1')
    child = omop_fact(identity='origin-component', row='2')
    child['parent'] = {'table': 'measurement', 'row_id': '1', 'person_key': 'synthetic-person',
                       'event_field_name': 'measurement.measurement_id',
                       'event_field_concept': {**concept('Metadata'), 'vocabulary_id': 'CDM',
                                               'concept_code': 'measurement.measurement_id'}}
    evidence.update(facts=[parent, child], current=['parent', 'origin-component'])
    return evidence


def test_source_only_linked_genomics_is_preserved_not_promoted_to_an_approved_target():
    evidence = linked_genomics()
    validate(evidence, 'PatientEvidence', context=context())
    assert evidence['facts'][0]['concept_id'] == '0'
    document = trial('histologic_types_required')
    document['native'] = ['0']
    with pytest.raises(ContractError):
        validate(document, 'TrialPredicate', context=context())


@pytest.mark.parametrize('mutation', ['person', 'parent_person', 'parent_table', 'orphan', 'event_type', 'event_name', 'event_code', 'self'])
def test_linked_components_cannot_cross_person_event_table_or_parent(mutation):
    evidence = linked_genomics()
    child = evidence['facts'][1]
    if mutation == 'person':
        child['person_key'] = 'another-person'
    if mutation == 'parent_person':
        child['parent']['person_key'] = 'another-person'
    if mutation == 'parent_table':
        child['parent']['table'] = 'observation'
    if mutation == 'orphan':
        child['parent']['row_id'] = '99'
    if mutation == 'event_type':
        child['parent']['event_field_concept']['vocabulary_id'] = 'LOINC'
    if mutation == 'event_name':
        child['parent']['event_field_name'] = 'observation.observation_id'
    if mutation == 'event_code':
        child['parent']['event_field_concept']['concept_code'] = 'observation.observation_id'
    if mutation == 'self':
        child['parent']['row_id'] = child['row_id']
    with pytest.raises(ContractError):
        validate(evidence, 'PatientEvidence', context=context())


def test_subject_is_not_positive_and_independent_axes_do_not_become_same_finding_across_fields():
    for field in ('mutation_genes_required', 'mutation_origins_required'):
        document = trial(field)
        envelope = document['native'][0]
        assert envelope['cross_axis_combination'] == 'independent_exists'
        assert envelope['terms'][0]['status_filter'] is None
        validate(document, 'TrialPredicate', context=context())
        envelope['terms'][0]['status_filter'] = 'present'
        with pytest.raises(ContractError):
            validate(document, 'TrialPredicate', context=context())


def test_exact_string_and_primary_concept_answers_are_distinct_and_both_need_bindings():
    document = trial('her2_statuses_required')
    term = document['native'][0]['terms'][0]
    assert term['answer']['value'] == 'Exact Synthetic Display Title'
    assert term['answer']['value'] != term['answer']['concept']['concept_code']
    validate(document, 'TrialPredicate', context=context())
    term.update(bound_answer('concept'))
    document['answer_bindings'] = [binding_claims(term)]
    validate(document, 'TrialPredicate', context=context())
    del term['answer']['binding_evidence']['choice_code_ids']
    with pytest.raises(ContractError):
        validate(document, 'TrialPredicate', context=context())


def test_conflicting_primary_bindings_are_a_blocked_claim_not_patient_negative():
    document = trial('her2_statuses_required')
    term = document['native'][0]['terms'][0]
    term.update(bound_answer('concept'))
    document['answer_bindings'] = [binding_claims(term)]
    other = deepcopy(document['answer_bindings'][0]['primary_codes'][0])
    other.update(concept_id='990000099', choice_code_id=2)
    document['answer_bindings'][0]['primary_codes'].append(other)
    with pytest.raises(ContractError, match='primary_binding_conflict'):
        validate(document, 'TrialPredicate', context=context())
    document.update(state='blocked', reason='binding_conflict', native=None)
    validate(document, 'TrialPredicate', context=context())


def test_concept_multiselect_is_not_invented_from_whole_string_lookup():
    document = trial('staging_modalities_required')
    term = document['native'][0]['terms'][0]
    term.update(bound_answer('concept'))
    document['answer_bindings'] = [binding_claims(term, multiple=True)]
    with pytest.raises(ContractError, match='concept_multiselect_writer_unsupported'):
        validate(document, 'TrialPredicate', context=context())
    document.update(state='blocked', reason='unsupported_representation', native=None)
    validate(document, 'TrialPredicate', context=context())


def test_encoded_negative_status_is_not_overlaid_with_positive_result():
    document = trial('her2_statuses_required')
    term = {'kind': 'assertion', 'concept': concept(), 'state': 'encoded_by_concept'}
    document['native'][0]['terms'] = [term]
    validate(document, 'TrialPredicate', context=context())
    term['status'] = 'present'
    with pytest.raises(ContractError):
        validate(document, 'TrialPredicate', context=context())


def test_sct_empty_terms_still_constrain_and_zero_days_is_not_blank():
    document = trial('stem_cell_transplant_history_excluded')
    document['source_companions']['day_since_stem_cell_transplant_required'] = 0
    envelope = document['native'][0]
    envelope['terms'] = []
    envelope['independent_requirements'] = {
        'event': concept('Procedure'), 'history_required': None, 'minimum_elapsed_days': 0,
        'elapsed_operator': 'gte', 'temporal_anchor': 'evaluation_date', 'polarity': 'required',
        'combination': 'and_with_exclusion_verdict', 'unknown_is_negative': False,
    }
    validate(document, 'TrialPredicate', context=context())
    document.update(state='unconstrained', native=[])
    with pytest.raises(ContractError, match='sct_independent_requirement_not_empty'):
        validate(document, 'TrialPredicate', context=context())


@pytest.mark.parametrize('field', MCL_GROUP)
def test_count_identity_minimum_and_combination_preserve_producer_semantics(field):
    document = trial(field)
    envelope = document['native'][0]
    document['source_companions']['high_risk_mcl_criteria_min_count'] = 0
    # Native CB emits (minimum or 1), not a newly designed zero-count rule.
    validate(document, 'TrialPredicate', context=context())
    assert envelope['minimum'] == 1
    other = deepcopy(envelope['terms'][0])
    other['rule_identity'] += '_different_source_rule_same_predicate'
    envelope['terms'].append(other)
    validate(document, 'TrialPredicate', context=context())
    other['rule_identity'] = envelope['terms'][0]['rule_identity']
    with pytest.raises(ContractError, match='duplicate_rule_identity'):
        validate(document, 'TrialPredicate', context=context())


@pytest.mark.parametrize('mutation', ['role', 'term_kind', 'schema', 'unknown', 'minimum', 'dependency', 'companion'])
def test_malformed_and_stale_claims_fail_closed(mutation):
    document = trial('high_risk_mcl_criteria_required')
    envelope = document['native'][0]
    if mutation == 'role':
        envelope['role'] = 'excluded'
    if mutation == 'term_kind':
        envelope['terms'] = [{'kind': 'assertion', 'concept': concept(), 'state': 'encoded_by_concept'}]
    if mutation == 'schema':
        envelope['schema'] = 'cb-trial-criteria-999'
    if mutation == 'unknown':
        envelope['unknown_is_negative'] = True
    if mutation == 'minimum':
        envelope['minimum'] = 0
    if mutation == 'dependency':
        document['dependencies'][1]['state'] = 'stale'
    if mutation == 'companion':
        document['source_companions'] = {}
    with pytest.raises(ContractError):
        validate(document, 'TrialPredicate', context=context())


@pytest.mark.parametrize('operator,args', [('divide', [1]), ('log10', [1, 2]), ('multiply', [True, 2]),
                                         ('if_gte', [1, 2]), ('not_a_cb_operation', [1, 2])])
def test_expression_arity_types_and_operation_names_are_not_arbitrary(operator, args):
    document = trial('high_risk_mcl_criteria_required')
    document['native'][0]['terms'][0]['predicate'] = {
        'kind': 'comparison', 'operand': {
            'kind': 'derived_score', 'algorithm': 'cb-mipi-hoster-1',
            'expression': {'operation': operator, 'arguments': args},
            'unknown_if_missing': [question('number')], 'unknown_if_nonpositive': [question('number')],
        }, 'operator': 'gte', 'value': 6.2,
    }
    with pytest.raises(ContractError):
        validate(document, 'TrialPredicate', context=context())


def test_unknown_properties_and_nonfinite_values_never_normalize_to_supported_claims():
    evidence = patient()
    evidence['ready'] = True
    with pytest.raises(ContractError):
        validate(evidence, 'PatientEvidence', context=context())
    del evidence['ready']
    evidence['facts'][0]['value'] = {'kind': 'number', 'value': float('nan'), 'unit': None}
    with pytest.raises(ContractError, match='not_finite_json'):
        validate(evidence, 'PatientEvidence', context=context())


def test_public_schemas_cannot_be_mutated_between_requests():
    schema = schema_for('TrialPredicate')
    schema['$defs']['TrialPredicate']['properties']['state'] = {'const': 'broken'}
    validate(trial('stages'), 'TrialPredicate', context=context())


@pytest.mark.parametrize('value,state', [(True, 'present'), (False, 'negative'), (None, 'unanswered')])
def test_language_capabilities_preserve_asked_state_without_moving_the_pair_builder(value, state):
    evidence = patient('languages_skills_required')
    fact = evidence['facts'][0]
    for key in ('projection', 'value'):
        del fact[key]
    fact.update(kind='language_capability', language_concept_id='990000001',
                skill_concept_id='990000002', value=value, state=state)
    evidence['state'] = state
    validate(evidence, 'PatientEvidence', context=context())
    fact['state'] = 'indeterminate'
    with pytest.raises(ContractError, match='language_capability_state'):
        validate(evidence, 'PatientEvidence', context=context())


@pytest.mark.parametrize('other_value,other_state', [(False, 'negative'), (None, 'unanswered')])
def test_independent_language_capabilities_can_be_mixed_without_false_conflict(other_value, other_state):
    evidence = patient('languages_skills_required')
    speak = evidence['facts'][0]
    for key in ('projection', 'value'):
        del speak[key]
    speak.update(kind='language_capability', language_concept_id='990000001',
                 skill_concept_id='990000002', value=True, state='present')
    write = deepcopy(speak)
    write.update(evidence_id='write-edit', skill_concept_id='990000003',
                 field_name='synthetic_write', value=other_value, state=other_state)
    evidence.update(facts=[speak, write], current=[speak['evidence_id'], write['evidence_id']], state='mixed')
    validate(evidence, 'PatientEvidence', context=context())
    evidence['state'] = 'conflict'
    with pytest.raises(ContractError, match='language_aggregate_state'):
        validate(evidence, 'PatientEvidence', context=context())
    write['skill_concept_id'] = speak['skill_concept_id']
    validate(evidence, 'PatientEvidence', context=context())


def test_athena_cdm_column_name_binding_is_not_rejected_as_a_legacy_code_mismatch():
    evidence = linked_genomics()
    event = evidence['facts'][1]['parent']['event_field_concept']
    event.update(concept_code='CDM114', concept_name='measurement.measurement_id')
    validate(evidence, 'PatientEvidence', context=context())
    event['concept_name'] = 'observation.observation_id'
    with pytest.raises(ContractError, match='event_field_column_conflict'):
        validate(evidence, 'PatientEvidence', context=context())


def test_derived_evidence_keeps_input_references_and_rejects_cycles_not_shared_inputs():
    evidence = patient('high_risk_mcl_criteria_required')
    source = evidence['facts'][0]
    source['value'] = {'kind': 'number', 'value': 0, 'unit': 'synthetic-unit'}
    derived = {key: deepcopy(source[key]) for key in (
        'person_key', 'state', 'effective_at', 'provenance')}
    derived.update(evidence_id='derived-1', kind='derived', owner='CB',
                   algorithm_version='synthetic-not-a-clinical-formula',
                   inputs=[source['evidence_id']], value={'kind': 'number', 'value': 0, 'unit': None})
    evidence['facts'].append(derived)
    evidence['current'].append(derived['evidence_id'])
    validate(evidence, 'PatientEvidence', context=context())
    other = deepcopy(derived)
    other.update(evidence_id='derived-2', inputs=['derived-1'])
    evidence['facts'].append(other)
    evidence['current'].append('derived-2')
    derived['inputs'] = ['derived-2']
    with pytest.raises(ContractError, match='derivation_cycle'):
        validate(evidence, 'PatientEvidence', context=context())


def test_physical_clear_is_not_an_empty_result_or_a_zero():
    evidence = patient()
    fact = omop_fact()
    fact['state'] = evidence['state'] = 'cleared'
    fact['answer'].update(value_as_string=None, value_source_value='PatientRecord:cleared')
    evidence.update(facts=[fact], current=[fact['evidence_id']])
    validate(evidence, 'PatientEvidence', context=context())
    fact['answer']['value_as_number'] = 0
    with pytest.raises(ContractError, match='physical_clear_marker'):
        validate(evidence, 'PatientEvidence', context=context())


def test_native_score_expression_and_all_of_have_typed_unknown_operands_without_evaluation():
    document = trial('high_risk_mcl_criteria_required')
    size = comparison()
    operand = question('number', '')  # Native producer preserves empty dimensionless units.
    score = {'kind': 'derived_score', 'algorithm': 'cb-mipi-hoster-1',
             'expression': {'operation': 'add', 'arguments': [
                 {'operation': 'multiply', 'arguments': [0.03535, operand]},
                 {'operation': 'log10', 'arguments': [operand]},
             ]}, 'unknown_if_missing': [operand], 'unknown_if_nonpositive': [operand]}
    document['native'][0]['terms'][0]['predicate'] = {
        'kind': 'all_of', 'terms': [size, {'kind': 'comparison', 'operand': score,
                                         'operator': 'gte', 'value': 6.2}],
        'unknown_if_missing': [operand], 'unknown_if_nonpositive': [operand],
    }
    validate(document, 'TrialPredicate', context=context())
    assert operand['unit'] == ''


@pytest.mark.parametrize('field,companion', [
    ('concomitant_medications_excluded', 'concomitant_medications_washout_period_duration'),
    ('stem_cell_transplant_history_excluded', 'day_since_stem_cell_transplant_required'),
])
def test_stale_companions_cannot_be_mislabelled_unconstrained(field, companion):
    document = trial(field)
    document.update(state='unconstrained', native=[])
    next(dep for dep in document['dependencies'] if dep['field'] == companion)['state'] = 'stale'
    with pytest.raises(ContractError, match='noncurrent_dependencies'):
        validate(document, 'TrialPredicate', context=context())


def test_physical_string_catalog_code_is_not_an_alias_for_the_display_literal():
    document = trial('her2_statuses_required')
    term = document['native'][0]['terms'][0]
    term['answer']['value'] = document['answer_bindings'][0]['catalog_code']
    with pytest.raises(ContractError, match='literal_is_not_catalog_title'):
        validate(document, 'TrialPredicate', context=context())


def test_no_remote_reference_or_client_ready_claim_can_be_smuggled_in():
    document = trial('stages')
    document['$ref'] = 'https://untrusted.invalid/predicate'
    with pytest.raises(ContractError):
        validate(document, 'TrialPredicate', context=context())
    del document['$ref']
    document['provenance']['assurance'] = 'certified'
    with pytest.raises(ContractError):
        validate(document, 'TrialPredicate', context=context())


def test_validation_diagnostics_do_not_echo_patient_values():
    evidence = patient()
    evidence['facts'][0]['value'] = {'kind': 'number', 'value': 'sensitive-patient-text', 'unit': None}
    with pytest.raises(ContractError) as error:
        validate(evidence, 'PatientEvidence', context=context())
    assert 'sensitive-patient-text' not in str(error.value)


def test_excessive_nesting_fails_before_recursive_schema_resolution():
    value = {'sensitive': 'synthetic'}
    for _ in range(40):
        value = {'nested': value}
    with pytest.raises(ContractError, match='document_limits'):
        validate(value, 'Context')


@pytest.mark.parametrize('location', ['effective_at', 'scope', 'provenance', 'context'])
def test_invalid_rfc3339_timestamps_are_rejected_without_optional_format_extras(location):
    document = patient()
    name = 'PatientEvidence'
    if location == 'effective_at':
        document['facts'][0]['effective_at'] = 'not-a-date-time'
    elif location == 'scope':
        document['scope']['as_of'] = '2026-10-30T00:00:00'  # Missing required offset.
    elif location == 'provenance':
        document['provenance']['captured_at'] = '2026-99-99T25:61:00Z'
    else:
        document, name = context_claims(), 'Context'
        document['provenance']['captured_at'] = 'invalid'
    with pytest.raises(ContractError, match='schema_'):
        validate(document, name)


def test_flat_long_derivation_graph_does_not_depend_on_python_recursion_limit():
    document = patient('high_risk_mcl_criteria_required')
    source = document['facts'][0]
    derived = []
    for index in range(1100):
        fact = {key: deepcopy(source[key]) for key in (
            'person_key', 'state', 'effective_at', 'provenance')}
        fact.update(evidence_id=f'derived-{index}', kind='derived', owner='CB',
                    algorithm_version='synthetic-not-a-clinical-formula',
                    inputs=[f'derived-{index + 1}' if index < 1099 else source['evidence_id']],
                    value={'kind': 'number', 'value': 0, 'unit': None})
        derived.append(fact)
    document['facts'] = derived + [source]
    document['current'] = [fact['evidence_id'] for fact in document['facts']]
    validate(document, 'PatientEvidence', context=context())
    derived[-1]['inputs'] = [derived[0]['evidence_id']]
    with pytest.raises(ContractError, match='derivation_cycle'):
        validate(document, 'PatientEvidence', context=context())
