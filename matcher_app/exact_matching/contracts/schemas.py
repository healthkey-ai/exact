"""Draft 2020-12 schemas for offline contract development, not clinical evaluation.

Native Trial payloads follow CB's cb-trial-criteria-1 grammar without rewriting
it. All references are local. These definitions neither load a vocabulary nor
certify the provenance, approval, completeness, or freshness of supplied claims.
"""
from copy import deepcopy

from .inventory import CRITERIA


def obj(properties, optional=()):
    return {'type': 'object', 'properties': properties,
            'required': [key for key in properties if key not in optional],
            'additionalProperties': False}


def ref(name):
    return {'$ref': '#/$defs/' + name}


def const(value):
    return {'const': value}


def enum(*values):
    return {'enum': list(values)}


def array(item, minimum=0, unique=False):
    return {'type': 'array', 'items': item, 'minItems': minimum, 'uniqueItems': unique}


def nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


TEXT = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}
STRING = {'type': 'string'}
ID = {'type': 'string', 'pattern': '^[1-9][0-9]*$'}
PHYSICAL_ID = {'type': 'string', 'pattern': '^(0|[1-9][0-9]*)$'}
NUMBER = {'type': 'number'}
BOOL = {'type': 'boolean'}
POSITIVE_INT = {'type': 'integer', 'minimum': 1}
NONNEGATIVE_INT = {'type': 'integer', 'minimum': 0}
HASH = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
SHA = {'type': 'string', 'pattern': '^[a-f0-9]{40}$'}
DATE = {'type': 'string', 'format': 'date'}
TIMESTAMP = {'type': 'string', 'format': 'date-time'}
FIELDS = enum(*(item.field for item in CRITERIA))
ROLE = enum('required', 'excluded', 'sufficient_any')
CLINICAL_STATE = enum('unanswered', 'present', 'asserted_none', 'negative',
                      'indeterminate', 'conflict', 'cleared', 'mixed')
FACT_STATE = enum('unanswered', 'present', 'asserted_none', 'negative', 'indeterminate', 'cleared')

CONCEPT_PROPERTIES = {
    'concept_id': ID, 'vocabulary_id': TEXT, 'concept_code': TEXT,
    'domain_id': TEXT, 'concept_class_id': nullable(TEXT),
}
DEFS = {
    'Concept': obj(CONCEPT_PROPERTIES, optional=('concept_class_id',)),
    'EventFieldConcept': obj({**CONCEPT_PROPERTIES, 'concept_name': TEXT},
                             optional=('concept_class_id', 'concept_name')),
    'Question': obj({
        **CONCEPT_PROPERTIES, 'source_value': TEXT,
        'omop_table': enum('measurement', 'observation'),
        'value_kind': enum('string', 'concept', 'number', 'boolean', 'date'),
        'unit': nullable(STRING),
    }, optional=('concept_class_id',)),
    'Provenance': obj({
        'issuer': TEXT, 'source_revision': SHA, 'captured_at': TIMESTAMP,
        'assurance': enum('synthetic', 'unverified', 'producer_reported'),
    }),
    'Context': obj({
        'schema': const('exact-matching-context-1'),
        'predicate_schema': const('exact-trial-predicate-1'),
        'patient_schema': const('exact-patient-evidence-1'),
        'registry_version': const('cb-trial-criteria-1'),
        'registry_sha256': HASH, 'mapping_sha256': HASH,
        'vocabulary_release': TEXT, 'vocabulary_sha256': HASH,
        'trial_generation': TEXT, 'authoring_generation': TEXT,
        'patient_snapshot': TEXT, 'evaluation_date': DATE,
        'trial_producer_revision': SHA, 'patient_producer_revision': SHA,
        'provenance': ref('Provenance'),
    }),
    'Dependency': obj({
        'field': TEXT, 'state': enum('current', 'stale', 'unavailable', 'blocked'),
        'source_sha256': nullable(HASH), 'generation': nullable(TEXT),
    }),
    'StringBinding': obj({
        'table': TEXT, 'term_id': POSITIVE_INT, 'source_mapping_id': POSITIVE_INT,
    }),
    'ConceptBinding': obj({
        'table': TEXT, 'term_id': POSITIVE_INT, 'source_mapping_id': POSITIVE_INT,
        'choice_code_ids': array(POSITIVE_INT, 1, True),
        'writer_literals': {**array(TEXT, 1, True), 'maxItems': 1},
    }),
    'Answer': {'oneOf': [
        obj({'kind': const('string'), 'concept': ref('Concept'), 'vocabulary': TEXT,
             'value': TEXT, 'match': enum('exact', 'comma_joined_member'),
             'binding_evidence': ref('StringBinding')}),
        obj({'kind': const('concept'), 'concept': ref('Concept'), 'vocabulary': TEXT,
             'binding_evidence': ref('ConceptBinding')}),
    ]},
    'BoundAnswer': obj({'question': ref('Question'), 'answer': ref('Answer')}),
    'AnswerBindingClaims': obj({
        'question': ref('Question'), 'multiple': BOOL,
        'table': TEXT, 'term_id': POSITIVE_INT, 'catalog_code': TEXT, 'title': TEXT,
        'source_mapping_id': POSITIVE_INT,
        'primary_codes': array(obj({
            'choice_code_id': POSITIVE_INT, 'is_primary': const(True), 'display': TEXT,
            'concept_id': ID, 'vocabulary_id': TEXT, 'concept_code': TEXT,
        })),
    }),
    'Assertion': obj({'kind': const('assertion'), 'concept': ref('Concept'),
                      'state': const('encoded_by_concept')}),
    'Finding': obj({
        'kind': const('finding'), 'feature': ref('Concept'),
        'status': obj({'question': ref('Question'),
                       'value': obj({'kind': const('string'), 'value': const('present')})}),
        'association': const('same_finding'),
    }),
    'GenomicSubject': obj({
        'kind': const('genomic_subject'), 'feature': ref('Concept'),
        'status_filter': const(None), 'association': const('same_finding'),
        'qualifier': ref('BoundAnswer'),
    }, optional=('qualifier',)),
    'CodedResult': obj({'kind': const('coded_result'), 'association': const('same_observation'),
                        'question': ref('Question'), 'answer': ref('Answer')}),
    'Condition': obj({
        'kind': const('condition'), 'concept': ref('Concept'), 'assertion': const('present'),
        'qualifiers': obj({'activity': const('active'), 'neoplastic': const(False)}),
        'index_disease': ref('Concept'),
        'association': enum('index_malignancy', 'different_malignancy'),
    }, optional=('qualifiers', 'index_disease', 'association')),
    'Transplant': obj({
        'kind': const('transplant_history'), 'concept': ref('Concept'),
        'relation': enum('prior', 'relapsed_after', 'completed_tandem', 'never', 'planned_before'),
        'transplant_type': enum(None, 'autologous', 'allogeneic'),
        'unknown_is_negative': const(False),
    }),
    'Concomitant': obj({
        'kind': const('concomitant_treatment'), 'concept': ref('Concept'),
        'outside_protocol': BOOL, 'unknown_date_policy': const('exclude_on_known_treatment'),
    }),
    'Comparison': {'oneOf': [
        obj({'kind': const('comparison'), 'question': ref('Question'),
             'operator': enum('gt', 'gte', 'lt', 'lte'), 'value': NUMBER, 'unit': TEXT}),
        obj({'kind': const('comparison'),
             'operand': {'oneOf': [ref('Question'), ref('DerivedScore')]},
             'operator': enum('gt', 'gte', 'lt', 'lte'), 'value': NUMBER}),
    ]},
    'AllOf': obj({
        'kind': const('all_of'), 'terms': array(ref('Comparison'), 1),
        'unknown_if_missing': array(ref('Question'), 1),
        'unknown_if_nonpositive': array(ref('Question'), 1),
    }),
    'Rule': obj({
        'kind': const('rule'), 'rule_identity': TEXT,
        'predicate': {'oneOf': [ref('Assertion'), ref('Finding'), ref('Comparison'), ref('AllOf')]},
    }),
    'DerivedScore': obj({
        'kind': const('derived_score'), 'algorithm': enum('cb-mipi-hoster-1', 'cb-smipi-hoster-1'),
        'expression': ref('Expression'), 'unknown_if_missing': array(ref('Question'), 1),
        'unknown_if_nonpositive': array(ref('Question'), 1),
    }),
    'SctRequirements': obj({
        'event': ref('Concept'), 'history_required': nullable(BOOL),
        'minimum_elapsed_days': nullable(NONNEGATIVE_INT),
        'elapsed_operator': const('gte'), 'temporal_anchor': const('evaluation_date'),
        'polarity': const('required'), 'combination': const('and_with_exclusion_verdict'),
        'unknown_is_negative': const(False),
    }),
}


def arguments(*items):
    return {'type': 'array', 'prefixItems': list(items), 'items': False,
            'minItems': len(items), 'maxItems': len(items)}


NUMERIC_OPERAND = {'oneOf': [NUMBER, ref('Question'), ref('Expression')]}
# An operation allowlist alone is insufficient: reject malformed arity/types.
DEFS['Expression'] = {'oneOf': [
    obj({'operation': const('add'), 'arguments': array(NUMERIC_OPERAND, 2)}),
    *(obj({'operation': const(operation), 'arguments': arguments(NUMERIC_OPERAND, NUMERIC_OPERAND)})
      for operation in ('multiply', 'divide', 'positive_or_default')),
    obj({'operation': const('log10'), 'arguments': arguments(NUMERIC_OPERAND)}),
    obj({'operation': const('if_gte'),
         'arguments': arguments(NUMERIC_OPERAND, NUMBER, NUMBER, NUMBER)}),
    obj({'operation': const('band_gte'),
         'arguments': arguments(NUMERIC_OPERAND, {**array(NUMBER, 3), 'maxItems': 3})}),
]}
DEFS['Term'] = {'oneOf': [ref(name) for name in (
    'Assertion', 'Finding', 'GenomicSubject', 'CodedResult', 'Condition',
    'Transplant', 'Concomitant', 'Rule',
)]}
DEFS['Envelope'] = obj({
    'schema': const('cb-trial-criteria-1'), 'role': ROLE,
    'operator': enum('any_of', 'at_least'), 'terms': array(ref('Term')),
    'unknown_is_negative': const(False),
    'cross_axis_combination': const('independent_exists'),
    'independent_requirements': ref('SctRequirements'),
    'minimum': POSITIVE_INT, 'group': const('high_risk_mcl'),
    'inclusion': const('required_or_sufficient_any'), 'exclusion_overrides': const(True),
}, optional=('cross_axis_combination', 'independent_requirements', 'minimum', 'group',
             'inclusion', 'exclusion_overrides'))
DEFS['TrialPredicate'] = obj({
    'schema': const('exact-trial-predicate-1'), 'context_id': HASH, 'field': FIELDS,
    'state': enum('constrained', 'unconstrained', 'blocked'),
    'reason': enum(None, 'missing_mapping', 'unsupported_representation', 'unsupported_schema',
                   'stale', 'unavailable', 'invalid_source', 'binding_conflict'),
    'native': nullable({'oneOf': [
        array(ID, unique=True),
        array({'type': 'string', 'pattern': '^[1-9][0-9]*:[1-9][0-9]*$'}, 1, True),
        {**array(ref('Envelope'), 1), 'maxItems': 1},
    ]}),
    'dependencies': array(ref('Dependency'), 1),
    'answer_bindings': array(ref('AnswerBindingClaims')),
    'source_companions': obj({
        'disease': nullable(TEXT),
        'day_since_stem_cell_transplant_required': nullable(NONNEGATIVE_INT),
        'stem_cell_transplant_history_required': nullable(BOOL),
        'concomitant_medications_washout_period_duration': nullable(NONNEGATIVE_INT),
        'high_risk_mcl_criteria_min_count': nullable(NONNEGATIVE_INT),
    }, optional=('disease', 'day_since_stem_cell_transplant_required',
                 'stem_cell_transplant_history_required',
                 'concomitant_medications_washout_period_duration',
                 'high_risk_mcl_criteria_min_count')),
    'provenance': ref('Provenance'),
})

DEFS['Value'] = {'oneOf': [
    obj({'kind': const('string'), 'value': STRING}),
    obj({'kind': const('number'), 'value': NUMBER, 'unit': nullable(TEXT)}),
    obj({'kind': const('boolean'), 'value': BOOL}),
    obj({'kind': const('date'), 'value': DATE}),
    obj({'kind': const('collection'), 'value': array(STRING)}),
    obj({'kind': const('clear'), 'value': const(None)}),
]}
FACT_BASE = {'evidence_id': TEXT, 'person_key': TEXT, 'state': FACT_STATE,
             'effective_at': TIMESTAMP, 'provenance': ref('Provenance')}
DEFS['OmopFact'] = obj({
    **FACT_BASE, 'kind': const('omop'),
    'table': enum('measurement', 'observation', 'condition_occurrence',
                  'procedure_occurrence', 'drug_exposure'),
    'row_id': ID, 'concept_id': PHYSICAL_ID, 'source_value': STRING,
    'is_erroneous': const(False),
    'answer': obj({'value_as_number': nullable(NUMBER), 'value_as_string': nullable(STRING),
                   'value_as_concept_id': nullable(PHYSICAL_ID), 'unit_source_value': nullable(TEXT),
                   'value_source_value': nullable(STRING)}),
    'parent': obj({'table': const('measurement'), 'row_id': ID, 'person_key': TEXT,
                   'event_field_name': const('measurement.measurement_id'),
                   'event_field_concept': ref('EventFieldConcept')}),
}, optional=('answer', 'parent'))
DEFS['RecordAssertion'] = obj({
    **FACT_BASE, 'kind': const('patient_record'), 'record_id': ID,
    'field_name': TEXT, 'revision_id': TEXT,
    'projection': enum('pending', 'unavailable', 'acknowledged'), 'value': ref('Value'),
})
DEFS['LanguageCapability'] = obj({
    **FACT_BASE, 'kind': const('language_capability'), 'record_id': ID,
    'field_name': TEXT, 'revision_id': TEXT, 'language_concept_id': ID,
    'skill_concept_id': ID, 'value': nullable(BOOL),
})
DEFS['DerivedEvidence'] = obj({
    **FACT_BASE, 'kind': const('derived'), 'owner': enum('PROMOP', 'CB'),
    'algorithm_version': TEXT, 'inputs': array(TEXT, 1, True), 'value': ref('Value'),
})
DEFS['PatientEvidence'] = obj({
    'schema': const('exact-patient-evidence-1'), 'context_id': HASH, 'field': FIELDS,
    'person_key': TEXT, 'availability': enum('available', 'unavailable', 'unsupported', 'missing_mapping'),
    'state': CLINICAL_STATE, 'completeness': enum('unknown', 'partial', 'complete'),
    'scope': obj({'disease_key': nullable(TEXT), 'index_event_key': nullable(TEXT),
                  'as_of': TIMESTAMP}),
    'facts': array({'oneOf': [ref('OmopFact'), ref('RecordAssertion'),
                             ref('LanguageCapability'), ref('DerivedEvidence')]}),
    'current': array(TEXT, unique=True), 'superseded': array(TEXT, unique=True),
    'issues': array(TEXT, unique=True), 'provenance': ref('Provenance'),
})


def schema_for(name):
    """Return an isolated schema document; callers cannot mutate the registry."""
    if name not in ('TrialPredicate', 'PatientEvidence', 'Context'):
        raise ValueError('Unknown contract schema')
    return deepcopy({'$schema': 'https://json-schema.org/draft/2020-12/schema',
                     '$ref': '#/$defs/' + name, '$defs': DEFS})
