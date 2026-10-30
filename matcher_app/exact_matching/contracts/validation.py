"""Shape and reference validation only: no clinical verdict or readiness proof."""
from dataclasses import dataclass
import hashlib
import json

from .inventory import CRITERIA, MCL_GROUP
from .schemas import schema_for


class ContractError(ValueError):
    """A stable, value-free diagnostic; do not log patient payloads on failure."""


def require(condition, code):
    if not condition:
        raise ContractError(code)


def canonical(document):
    """Only JSON data is accepted; never coerce booleans, nulls, or empty values."""
    visited = 0
    def check(value, depth=0):
        nonlocal visited
        visited += 1
        require(depth <= 32 and visited <= 50000, 'document_limits')
        require(type(value) in (dict, list, str, int, float, bool, type(None)), 'not_json')
        if type(value) is dict:
            require(all(type(key) is str for key in value), 'not_json')
            for item in value.values():
                check(item, depth + 1)
        elif type(value) is list:
            for item in value:
                check(item, depth + 1)
    try:
        check(document)
        return json.dumps(document, allow_nan=False, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError('not_finite_json') from None


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def _question(question, kind=None):
    require(question['domain_id'].lower() == question['omop_table'], 'question_table_domain')
    if kind:
        require(question['value_kind'] == kind, 'question_value_kind')


def _bound_answer(node, bindings):
    question, answer = node['question'], node['answer']
    _question(question, answer['kind'])
    if answer['kind'] == 'string' and answer['match'] == 'comma_joined_member':
        require(',' not in answer['value'], 'ambiguous_multiselect_literal')
    receipt = answer['binding_evidence']
    matches = [binding for binding in bindings if binding['question'] == question
               and all(binding[key] == receipt[key] for key in ('table', 'term_id', 'source_mapping_id'))]
    require(len(matches) == 1, 'answer_binding_missing_or_ambiguous')
    binding = matches[0]
    if answer['kind'] == 'string':
        require(answer['value'] == binding['title'], 'literal_is_not_catalog_title')
        require(answer['match'] == ('comma_joined_member' if binding['multiple'] else 'exact'),
                'string_membership_shape')
    else:
        require(not binding['multiple'], 'concept_multiselect_writer_unsupported')
        primary = [row for row in binding['primary_codes'] if row['display'] == binding['title']]
        require(bool(primary), 'primary_binding_missing')
        target = answer['concept']
        require(all(all(row[key] == target[key] for key in ('concept_id', 'vocabulary_id', 'concept_code'))
                    for row in primary), 'primary_binding_conflict')
        require(set(receipt['choice_code_ids']) == {row['choice_code_id'] for row in primary},
                'primary_binding_identity')
        require(receipt['writer_literals'] == [binding['title']], 'primary_writer_literal')
    # Binding receipts are claims. Existence, unique primary resolution and
    # approval must be verified against trusted producer receipts in #662.


def _native_terms(native):
    if native and type(native[0]) is dict:
        return native[0]
    return None


def _trial(document, context):
    spec = next(item for item in CRITERIA if item.field == document['field'])
    dependencies = document['dependencies']
    names = [item['field'] for item in dependencies]
    require(len(names) == len(set(names)) and set(names) == set(spec.dependency_closure),
            'dependency_closure')
    require(set(document['source_companions']) == set(spec.companions), 'companion_fields')
    native, state = document['native'], document['state']
    if state == 'blocked':
        require(native is None and document['reason'] is not None, 'blocked_payload')
        return
    require(document['reason'] is None and native is not None, 'available_payload')
    require(all(item['state'] == 'current' and item['source_sha256'] is not None
                and item['generation'] is not None for item in dependencies), 'noncurrent_dependencies')
    if context is not None:
        require(all(item['generation'] == context.as_claims()['authoring_generation']
                    for item in dependencies), 'dependency_generation')
    if state == 'unconstrained':
        require(native == [], 'unconstrained_payload')
        if spec.operator == 'temporal_sct':
            companions = document['source_companions']
            require(companions['day_since_stem_cell_transplant_required'] is None
                    and companions['stem_cell_transplant_history_required'] in (None, False),
                    'sct_independent_requirement_not_empty')
        return
    require(bool(native), 'empty_constraint')
    envelope = _native_terms(native)
    if spec.representation == 'concept_set':
        require(all(type(item) is str and ':' not in item for item in native), 'native_concept_set')
        return
    if spec.representation == 'language_pair':
        require(all(type(item) is str and ':' in item for item in native), 'native_language_pair')
        return
    require(envelope is not None, 'native_envelope')
    require(envelope['role'] == spec.role, 'field_role')
    allowed_terms = {
        'condition': ('condition',), 'temporal_sct': ('transplant_history',),
        'finding': ('finding', 'assertion'), 'genomic_subject': ('genomic_subject',),
        'bound_origin': ('genomic_subject',), 'bound_interpretation': ('genomic_subject',),
        'coded_result': ('coded_result', 'assertion'), 'counted_rule': ('rule',),
        'medication': ('concomitant_treatment',),
    }[spec.operator]
    terms = envelope['terms']
    require(all(term['kind'] in allowed_terms for term in terms), 'field_term_kind')
    require(bool(terms) or 'independent_requirements' in envelope, 'empty_envelope')
    for term in terms:
        if spec.operator in ('bound_origin', 'bound_interpretation'):
            require('qualifier' in term, 'bound_qualifier_missing')
        elif spec.operator == 'genomic_subject':
            require('qualifier' not in term, 'unexpected_bound_qualifier')
    if spec.group_fields and spec.field.startswith('mutation_'):
        require(envelope.get('cross_axis_combination') == 'independent_exists', 'cross_axis_semantics')
    else:
        require('cross_axis_combination' not in envelope, 'unexpected_cross_axis')
    if spec.field in MCL_GROUP:
        require(envelope['operator'] == 'at_least', 'count_operator')
        require(all(key in envelope for key in ('minimum', 'group', 'inclusion', 'exclusion_overrides')),
                'count_combination_missing')
        minimum = document['source_companions']['high_risk_mcl_criteria_min_count']
        expected = (minimum or 1) if spec.role == 'required' else 1
        require(envelope['minimum'] == expected, 'count_minimum')
    else:
        require(envelope['operator'] == 'any_of', 'native_operator')
        require(not {'minimum', 'group', 'inclusion', 'exclusion_overrides'} & envelope.keys(),
                'unexpected_count_combination')
    if spec.operator == 'counted_rule':
        identities = [term['rule_identity'] for term in terms]
        require(len(identities) == len(set(identities)), 'duplicate_rule_identity')
        require(all(identity.startswith(f'CancerBot:{spec.family}:')
                    and len(identity) > len(f'CancerBot:{spec.family}:') for identity in identities),
                'rule_identity_family')
    if spec.operator == 'temporal_sct':
        companions = document['source_companions']
        minimum = companions['day_since_stem_cell_transplant_required']
        history = companions['stem_cell_transplant_history_required']
        needed = minimum is not None or history not in (None, False)
        require(('independent_requirements' in envelope) == needed, 'sct_companion_semantics')
        if needed:
            requirements = envelope['independent_requirements']
            require(requirements['minimum_elapsed_days'] == minimum
                    and requirements['history_required'] is history, 'sct_companion_values')
    else:
        require('independent_requirements' not in envelope, 'unexpected_independent_requirements')
    if spec.operator == 'medication':
        require(document['source_companions']['concomitant_medications_washout_period_duration'] is None,
                'washout_authority_unresolved')
    for node in walk(envelope):
        if 'omop_table' in node:
            _question(node)
        if node.get('kind') == 'coded_result' or ('question' in node and 'answer' in node):
            _bound_answer(node, document['answer_bindings'])
        if node.get('kind') == 'finding':
            _question(node['status']['question'], 'string')
        if node.get('kind') == 'condition':
            require(('association' in node) == ('index_disease' in node), 'index_disease_association')
        if node.get('kind') == 'comparison':
            operand = node.get('question', node.get('operand'))
            if 'omop_table' in operand:
                _question(operand, 'number')
                if 'unit' in node:
                    require(operand['unit'] == node['unit'], 'comparison_unit')
        if node.get('kind') in ('derived_score', 'all_of'):
            for operand in node['unknown_if_missing'] + node['unknown_if_nonpositive']:
                _question(operand, 'number')
        if 'operation' in node:
            for question in walk(node['arguments']):
                if 'omop_table' in question:
                    _question(question, 'number')
            if node['operation'] == 'band_gte':
                thresholds = node['arguments'][1]
                require(thresholds == sorted(set(thresholds)), 'unordered_bands')


def _patient(document):
    facts = document['facts']
    ids = [fact['evidence_id'] for fact in facts]
    require(len(ids) == len(set(ids)), 'duplicate_evidence_id')
    by_id = dict(zip(ids, facts))
    current, superseded = set(document['current']), set(document['superseded'])
    require(not current & superseded and current | superseded == set(ids), 'evidence_selection')
    require(all(fact['person_key'] == document['person_key'] for fact in facts), 'cross_person_evidence')
    physical = {(fact['table'], fact['row_id']): fact for fact in facts if fact['kind'] == 'omop'}
    require(len(physical) == sum(fact['kind'] == 'omop' for fact in facts), 'duplicate_physical_identity')
    for fact in facts:
        if fact['kind'] == 'omop':
            if fact['table'] in ('measurement', 'observation'):
                require('answer' in fact, 'physical_answer_missing')
            else:
                require('answer' not in fact and 'parent' not in fact, 'occurrence_not_scalar_answer')
            if 'parent' in fact:
                parent = fact['parent']
                require(parent['person_key'] == document['person_key'], 'cross_person_parent')
                require(parent['event_field_concept']['vocabulary_id'] == 'CDM', 'event_field_identity')
                target = physical.get((parent['table'], parent['row_id']))
                require(target is not None and target is not fact, 'parent_missing_or_self')
                require('parent' not in target, 'nested_genomic_parent')
            if fact['state'] == 'cleared':
                require(fact.get('answer', {}).get('value_source_value') == 'PatientRecord:cleared'
                        and all(fact['answer'][key] is None for key in (
                            'value_as_number', 'value_as_string', 'value_as_concept_id')),
                        'physical_clear_marker')
        if fact['kind'] in ('patient_record', 'derived'):
            require((fact['state'] == 'cleared') == (fact['value']['kind'] == 'clear'), 'clear_value_state')
        if fact['kind'] == 'language_capability':
            expected = ('unanswered' if fact['value'] is None else
                        'present' if fact['value'] is True else 'negative')
            require(fact['state'] == expected, 'language_capability_state')
        if fact['kind'] == 'derived':
            require(set(fact['inputs']) <= by_id.keys() and fact['evidence_id'] not in fact['inputs'],
                    'derivation_inputs')
    # Reject reference cycles, not repeated use of the same clinical evidence.
    completed = set()
    def visit(identity, ancestors):
        require(identity not in ancestors, 'derivation_cycle')
        if identity in completed:
            return
        fact = by_id[identity]
        if fact['kind'] == 'derived':
            for source in fact['inputs']:
                visit(source, ancestors | {identity})
        completed.add(identity)
    for identity in ids:
        visit(identity, set())
    if document['availability'] != 'available':
        require(bool(document['issues']), 'unavailable_reason_missing')
        return
    state = document['state']
    selected = [by_id[identity] for identity in document['current']]
    if state == 'unanswered':
        require(all(fact['state'] == 'unanswered' for fact in selected), 'unanswered_has_current_evidence')
    elif state == 'conflict':
        require(len(selected) >= 2, 'conflict_evidence_missing')
    else:
        require(bool(selected) and all(fact['state'] == state for fact in selected), 'selected_state')
    if state in ('asserted_none', 'negative'):
        require(document['completeness'] != 'unknown', 'negative_scope_unknown')


def validate(document, name, *, context=None):
    """Validate claims, never attest them. No implicit normalization or DB access.

    Install exact-matching[contracts] for this optional development validator.
    Diagnostics intentionally omit values, JSON paths and person identifiers.
    """
    from jsonschema import Draft202012Validator, FormatChecker

    canonical(document)
    validator = Draft202012Validator(schema_for(name), format_checker=FormatChecker())
    try:
        validator.validate(document)
    except RecursionError:
        raise ContractError('excessive_nesting') from None
    except Exception as exc:
        from jsonschema.exceptions import ValidationError
        if isinstance(exc, ValidationError):
            raise ContractError('schema_' + name) from None
        raise
    if context is not None:
        require(isinstance(context, MatchingContractContext), 'context_type')
        require(document.get('context_id') == context.context_id, 'context_identity')
    if name == 'TrialPredicate':
        _trial(document, context)
    elif name == 'PatientEvidence':
        _patient(document)


@dataclass(frozen=True)
class MatchingContractContext:
    """Immutable identity claims, NOT a trusted release/readiness attestation.

    All nested data is stored as canonical JSON, not caller-owned mutable dicts.
    A new vocabulary/mapping/registry/authoring generation yields a new identity.
    #662 must bind these claims to authenticated producer receipts before use.
    """
    _canonical_claims: str

    def __post_init__(self):
        try:
            document = json.loads(self._canonical_claims)
        except (TypeError, ValueError):
            raise ContractError('context_json') from None
        validate(document, 'Context')
        object.__setattr__(self, '_canonical_claims', canonical(document))

    @classmethod
    def from_claims(cls, document):
        return cls(canonical(document))

    @property
    def context_id(self):
        return hashlib.sha256(self._canonical_claims.encode()).hexdigest()

    def as_claims(self):
        return json.loads(self._canonical_claims)
