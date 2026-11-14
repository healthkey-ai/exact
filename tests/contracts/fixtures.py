"""Entirely synthetic claims. These IDs/receipts are NOT vocabulary approvals.

The examples exercise grammar, not clinical correctness, installed capabilities,
snapshot membership, materialization readiness or a patient–Trial verdict.
"""
from exact_matching.contracts import MatchingContractContext
from exact_matching.contracts.inventory import CRITERIA, CB_PRODUCER, PROMOP_PRODUCER


PROVENANCE = {
    'issuer': 'synthetic-contract-fixtures-only', 'source_revision': 'f' * 40,
    'captured_at': '2026-10-30T00:00:00Z', 'assurance': 'synthetic',
}


def context_claims():
    return {
        'schema': 'exact-matching-context-1', 'predicate_schema': 'exact-trial-predicate-1',
        'patient_schema': 'exact-patient-evidence-1', 'registry_version': 'cb-trial-criteria-1',
        'registry_sha256': '1' * 64, 'mapping_sha256': '2' * 64,
        'vocabulary_release': 'synthetic-not-a-certified-release', 'vocabulary_sha256': '3' * 64,
        'trial_generation': 'synthetic-trials-1', 'authoring_generation': 'synthetic-authoring-1',
        'patient_snapshot': 'synthetic-person-snapshot-1', 'evaluation_date': '2026-10-30',
        'trial_producer_revision': CB_PRODUCER, 'patient_producer_revision': PROMOP_PRODUCER,
        'provenance': dict(PROVENANCE),
    }


def context():
    return MatchingContractContext.from_claims(context_claims())


def concept(domain='Condition'):
    return {'concept_id': '990000001', 'vocabulary_id': 'SYNTHETIC',
            'concept_code': 'not-a-clinical-target', 'domain_id': domain,
            'concept_class_id': 'synthetic-only'}


def question(kind='string', unit=None, source='synthetic:question'):
    return {**concept('Measurement'), 'source_value': source,
            'omop_table': 'measurement', 'value_kind': kind, 'unit': unit}


def bound_answer(kind='string'):
    binding = {'table': 'synthetic_catalog', 'term_id': 1, 'source_mapping_id': 1}
    answer = {'kind': kind, 'concept': concept('Meas Value'), 'vocabulary': 'SyntheticAnswer'}
    if kind == 'string':
        answer.update(value='Exact Synthetic Display Title', match='exact', binding_evidence=binding)
    else:
        answer['binding_evidence'] = {
            **binding, 'choice_code_ids': [1], 'writer_literals': ['Exact Synthetic Display Title'],
        }
    return {'question': question(kind), 'answer': answer}


def comparison():
    return {'kind': 'comparison', 'question': question('number', 'cm'),
            'operator': 'gte', 'value': 5, 'unit': 'cm'}


def term_for(spec):
    operator = spec.operator
    if operator == 'condition':
        return {'kind': 'condition', 'concept': concept(), 'assertion': 'present'}
    if operator == 'temporal_sct':
        return {'kind': 'transplant_history', 'concept': concept('Procedure'), 'relation': 'prior',
                'transplant_type': None, 'unknown_is_negative': False}
    if operator == 'finding':
        return {'kind': 'finding', 'feature': concept('Observation'),
                'association': 'same_finding',
                'status': {'question': question(source='genomics:status'),
                           'value': {'kind': 'string', 'value': 'present'}}}
    if operator in ('genomic_subject', 'bound_origin', 'bound_interpretation'):
        subject = {'kind': 'genomic_subject', 'feature': concept('Observation'),
                   'status_filter': None, 'association': 'same_finding'}
        if operator != 'genomic_subject':
            subject['qualifier'] = bound_answer()
        return subject
    if operator == 'coded_result':
        return {'kind': 'coded_result', 'association': 'same_observation', **bound_answer()}
    if operator == 'medication':
        return {'kind': 'concomitant_treatment', 'concept': concept('Drug'),
                'outside_protocol': False, 'unknown_date_policy': 'exclude_on_known_treatment'}
    if operator == 'counted_rule':
        return {'kind': 'rule', 'rule_identity': f'CancerBot:{spec.family}:synthetic_rule',
                'predicate': comparison()}
    raise AssertionError(operator)


def trial(field):
    spec = next(item for item in CRITERIA if item.field == field)
    if spec.representation == 'concept_set':
        native = ['990000001']
    elif spec.representation == 'language_pair':
        native = ['990000001:990000002']
    else:
        envelope = {'schema': 'cb-trial-criteria-1', 'role': spec.role,
                    'operator': 'any_of', 'terms': [term_for(spec)], 'unknown_is_negative': False}
        if field.startswith('mutation_'):
            envelope['cross_axis_combination'] = 'independent_exists'
        if field.startswith('high_risk_mcl_criteria_'):
            envelope.update(operator='at_least', minimum=1, group='high_risk_mcl',
                            inclusion='required_or_sufficient_any', exclusion_overrides=True)
        native = [envelope]
    companions = {name: None for name in spec.companions}
    if 'disease' in companions:
        companions['disease'] = 'synthetic-index-disease'
    bindings = []
    if spec.operator in ('coded_result', 'bound_origin', 'bound_interpretation'):
        bound = native[0]['terms'][0]
        if 'qualifier' in bound:
            bound = bound['qualifier']
        bindings.append(binding_claims(bound))
    return {
        'schema': 'exact-trial-predicate-1', 'context_id': context().context_id,
        'field': field, 'state': 'constrained', 'reason': None, 'native': native,
        'dependencies': [{'field': name, 'state': 'current', 'source_sha256': '4' * 64,
                          'generation': 'synthetic-authoring-1'} for name in spec.dependency_closure],
        'source_companions': companions, 'provenance': dict(PROVENANCE),
        'answer_bindings': bindings,
    }


def binding_claims(bound, *, multiple=False):
    answer = bound['answer']
    target = answer['concept']
    return {'question': dict(bound['question']), 'multiple': multiple,
            'table': 'synthetic_catalog', 'term_id': 1, 'catalog_code': 'synthetic-code-not-title',
            'title': 'Exact Synthetic Display Title', 'source_mapping_id': 1,
            'primary_codes': [] if answer['kind'] == 'string' else [{
                'choice_code_id': 1, 'is_primary': True, 'display': 'Exact Synthetic Display Title',
                **{key: target[key] for key in ('concept_id', 'vocabulary_id', 'concept_code')},
            }]}


def record_fact(value=None, *, state='present', identity='record-edit'):
    return {'evidence_id': identity, 'person_key': 'synthetic-person', 'state': state,
            'effective_at': '2026-10-30T00:00:00Z', 'provenance': dict(PROVENANCE),
            'kind': 'patient_record', 'record_id': '1', 'field_name': 'synthetic_field',
            'revision_id': 'synthetic-revision-1', 'projection': 'pending',
            'value': value if value is not None else {'kind': 'string', 'value': 'Exact Synthetic Display Title'}}


def omop_fact(*, identity='measurement-1', row='1'):
    return {'evidence_id': identity, 'person_key': 'synthetic-person', 'state': 'present',
            'effective_at': '2026-10-29T00:00:00Z', 'provenance': dict(PROVENANCE),
            'kind': 'omop', 'table': 'measurement', 'row_id': row,
            'concept_id': '0', 'source_value': 'synthetic:source-only', 'is_erroneous': False,
            'answer': {'value_as_number': None, 'value_as_string': 'synthetic raw answer',
                       'value_as_concept_id': None, 'unit_source_value': None, 'value_source_value': None}}


def patient(field='her2_statuses_required'):
    fact = record_fact()
    return {'schema': 'exact-patient-evidence-1', 'context_id': context().context_id,
            'field': field, 'person_key': 'synthetic-person', 'availability': 'available',
            'state': 'present', 'completeness': 'partial',
            'scope': {'disease_key': 'synthetic-index-disease', 'index_event_key': None,
                      'as_of': '2026-10-30T00:00:00Z'},
            'facts': [fact], 'current': [fact['evidence_id']], 'superseded': [],
            'issues': [], 'provenance': dict(PROVENANCE)}
