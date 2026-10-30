"""Offline evaluation of v1 claims, NOT authenticated matching or readiness.

No request route imports this module. #662 must authenticate producer receipts;
#661's host orchestration/score seam is still required before runtime use. The
reader never queries patient/catalog tables, expands concepts or derives scores.
"""
from dataclasses import dataclass
import json
import operator

from .contracts import ContractError, MatchingContractContext, validate
from .contracts.inventory import CRITERIA, MCL_GROUP, SOURCES
from .contracts.validation import canonical
from .omop.languages_verdict import language_verdict


@dataclass(frozen=True)
class CriterionResult:
    field: str
    status: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Truth:
    value: bool | None = None
    system: str | None = None
    reasons: tuple[str, ...] = ()


def _system(status, reason):
    return _Truth(system=status, reasons=(reason,))


def _reduce(values, minimum):
    """Preflight ALL operands: unsupported cannot disappear inside OR or count."""
    values = tuple(values)
    reasons = tuple(sorted({reason for value in values for reason in value.reasons}))
    for status in ('unsupported', 'unavailable'):
        if any(value.system == status for value in values):
            return _Truth(system=status, reasons=reasons)
    yes = sum(value.value is True for value in values)
    unknown = sum(value.value is None for value in values)
    truth = True if yes >= minimum else False if yes + unknown < minimum else None
    return _Truth(truth, reasons=reasons)


def _invert(value):
    return _Truth(None if value.value is None else not value.value, value.system, value.reasons)


def _result(field, truth):
    status = truth.system or ('patient_unknown' if truth.value is None else
                              'satisfied' if truth.value else 'violated')
    return CriterionResult(field, status, truth.reasons)


# Explicit operator allowlist, frozen scope comes from the reviewed #660 inventory.
# A missing physical/clinical binding is not implementation permission to guess it.
_OPERATORS = {
    'concept_set': '_concept_set', 'language_pair': '_language',
    'condition': '_envelope', 'temporal_sct': '_envelope',
    'finding': '_envelope', 'genomic_subject': '_envelope',
    'bound_origin': '_envelope', 'bound_interpretation': '_envelope',
    'coded_result': '_envelope', 'counted_rule': '_envelope',
    'medication': '_envelope',
}
FIELD_READERS = {spec.field: _OPERATORS[spec.operator] for spec in CRITERIA}
_SPECS = {spec.field: spec for spec in CRITERIA}
_TABLES = {'Measurement': 'measurement', 'Observation': 'observation',
           'Condition': 'condition_occurrence', 'Procedure': 'procedure_occurrence',
           'Drug': 'drug_exposure'}
_COMPARISONS = {'gt': operator.gt, 'gte': operator.ge, 'lt': operator.lt, 'lte': operator.le}
_DOCUMENT_BYTES = 1_000_000
_SESSION_BYTES = 16_000_000


def _snapshot(document):
    encoded = canonical(document)
    if len(encoded.encode()) > _DOCUMENT_BYTES:
        raise ContractError('reader_document_budget')
    return encoded


class ClaimsEvaluation:
    """One immutable, bounded patient snapshot for OFFLINE predicate evaluation.

    Constructor errors are value-free ContractErrors. Results contain no patient
    values or identifiers. Cache keys include full predicate bytes, not Trial ID.
    This class deliberately has no eligible/search/score/readiness interface.
    """

    def __init__(self, context, evidence, *, max_evaluations=10000):
        if not isinstance(context, MatchingContractContext):
            raise ContractError('context_type')
        if type(max_evaluations) is not int or max_evaluations < 1:
            raise ContractError('evaluation_budget')
        # Bound the whole snapshot, not only each individual document.
        snapshot = json.loads(_snapshot(evidence))
        if type(snapshot) is not list or len(snapshot) > len(CRITERIA):
            raise ContractError('evidence_inventory')
        documents = {}
        identities = set()
        for document in snapshot:
            validate(document, 'PatientEvidence', context=context)
            if document['field'] in documents:
                raise ContractError('duplicate_evidence_field')
            documents[document['field']] = document
            identities.add((document['person_key'], canonical(document['scope'])))
        if len(identities) > 1:
            raise ContractError('evidence_snapshot_identity')
        self._context = context
        self._documents = documents
        self._cache = {}
        self._max_evaluations = max_evaluations
        self._predicate_bytes = 0

    def evaluate(self, predicate):
        encoded = _snapshot(predicate)
        if encoded in self._cache:
            return self._cache[encoded]
        if len(self._cache) >= self._max_evaluations:
            raise ContractError('evaluation_budget')
        size = len(encoded.encode())
        if self._predicate_bytes + size > _SESSION_BYTES:
            raise ContractError('reader_session_budget')
        document = json.loads(encoded)
        validate(document, 'TrialPredicate', context=self._context)
        field = document['field']
        if document['state'] == 'blocked':
            result = CriterionResult(field, 'unavailable', (document['reason'],))
        elif document['state'] == 'unconstrained':
            result = CriterionResult(field, 'unconstrained')
        elif field not in self._documents:
            result = CriterionResult(field, 'unavailable', ('evidence_missing',))
        else:
            reader = _FieldReader(document, self._documents[field])
            truth = reader.evaluate()
            result = _result(field, truth)
        self._cache[encoded] = result
        self._predicate_bytes += size
        return result

    def mcl_group(self, predicates):
        """Combine source-rule counts, not patient codes, without inventing a score."""
        snapshot = json.loads(_snapshot(predicates))
        if type(snapshot) is not list or len(snapshot) != len(MCL_GROUP):
            raise ContractError('mcl_group_closure')
        # Validate first so no ambiguous duplicate/sibling can be silently lost.
        for document in snapshot:
            validate(document, 'TrialPredicate', context=self._context)
        if {item['field'] for item in snapshot} != set(MCL_GROUP):
            raise ContractError('mcl_group_closure')
        if len({canonical(item['source_companions']) for item in snapshot}) != 1:
            raise ContractError('mcl_companion_identity')
        dependencies = {}
        for item in snapshot:
            for dependency in item['dependencies']:
                key = dependency['field']
                encoded = canonical(dependency)
                if key in dependencies and dependencies[key] != encoded:
                    raise ContractError('mcl_dependency_identity')
                dependencies[key] = encoded
        results = {item['field']: self.evaluate(item) for item in snapshot}
        active = {item['field']: item['state'] != 'unconstrained' for item in snapshot}
        if not any(active.values()):
            return CriterionResult('high_risk_mcl', 'unconstrained')

        def truth(field):
            result = results[field]
            if result.status in ('unsupported', 'unavailable'):
                return _Truth(system=result.status, reasons=result.reasons)
            return _Truth({'satisfied': True, 'violated': False,
                           'patient_unknown': None, 'unconstrained': True}[result.status],
                          reasons=result.reasons)

        required, excluded, sufficient = MCL_GROUP
        inclusions = [truth(field) for field in (required, sufficient) if active[field]]
        inclusion = _reduce(inclusions, 1) if inclusions else _Truth(True)
        # Field-level exclusion has already been inverted to constraint polarity.
        combined = _reduce((inclusion, truth(excluded)), 2)
        if combined.system and results[excluded].status == 'violated':
            combined = _Truth(system=combined.system,
                              reasons=tuple(sorted({*combined.reasons, 'known_exclusion_violation'})))
        return _result('high_risk_mcl', combined)


class _FieldReader:
    def __init__(self, predicate, patient):
        self.predicate = predicate
        self.patient = patient
        self.spec = _SPECS[predicate['field']]
        current = set(patient['current'])
        self.selected = tuple(fact for fact in patient['facts'] if fact['evidence_id'] in current)
        self.physical = tuple(fact for fact in self.selected if fact['kind'] == 'omop')
        self.by_physical = {(fact['table'], fact['row_id']): fact for fact in self.physical}

    def evaluate(self):
        availability = self.patient['availability']
        if availability != 'available':
            status = 'unsupported' if availability == 'unsupported' else 'unavailable'
            return _system(status, 'patient_' + availability)
        for fact in self.physical:
            if 'parent' in fact and ('measurement', fact['parent']['row_id']) not in self.by_physical:
                return _system('unavailable', 'selected_parent_missing')
        # A pending assertion may supersede a projection. Never read around it.
        if any(fact['kind'] == 'patient_record' and fact['state'] != 'cleared'
               for fact in self.selected):
            return _system('unsupported', 'record_operand_binding_missing')
        if any(fact['kind'] == 'derived' for fact in self.selected):
            return _system('unsupported', 'derived_output_binding_missing')
        truth = getattr(self, FIELD_READERS[self.spec.field])()
        if truth.system is not None:
            return truth
        if self.patient['state'] in ('unanswered', 'cleared', 'indeterminate', 'conflict'):
            return _Truth(reasons=('patient_' + self.patient['state'],))
        return truth

    def _concept_set(self):
        # Bare physical IDs lack regimen/class/planned/ethnicity role binding.
        return _system('unsupported', 'concept_role_binding_missing')

    def _language(self):
        if any(fact['kind'] != 'language_capability' for fact in self.selected):
            return _system('unsupported', 'language_capability_binding_missing')
        held, asked = set(), set()
        for fact in self.selected:
            if fact['field_name'] not in SOURCES['language'].operands:
                return _system('unsupported', 'language_source_binding_missing')
            if fact['value'] is not None:
                asked.add(fact['language_concept_id'])
            if fact['value'] is True and fact['field_name'].endswith(('_speak', '_write')):
                held.add(f"{fact['language_concept_id']}:{fact['skill_concept_id']}")
        verdict = language_verdict(self.predicate['native'], held, asked)
        return _Truth({'matched': True, 'not_matched': False, 'unknown': None}[verdict])

    def _envelope(self):
        envelope = self.predicate['native'][0]
        values = [self._term(term) for term in envelope['terms']]
        # Empty SCT terms are neutral only for the exclusion part, not companions.
        truth = _reduce(values, envelope.get('minimum', 1)) if values else _Truth(False)
        if self.spec.role == 'excluded':
            truth = _invert(truth)
        if 'independent_requirements' in envelope:
            truth = _reduce((truth, _system('unsupported', 'sct_event_date_binding_missing')), 2)
        return truth

    def _term(self, term):
        kind = term['kind']
        if kind == 'rule':
            return self._term(term['predicate'])
        if kind == 'comparison':
            operand = term.get('question', term.get('operand'))
            if operand.get('kind') == 'derived_score':
                return _system('unsupported', 'stored_derived_binding_missing')
            value, failure = self._number(operand)
            return failure or _Truth(None if value is None else
                                     _COMPARISONS[term['operator']](value, term['value']))
        if kind == 'all_of':
            values = [self._term(item) for item in term['terms']]
            unknown_guard = False
            for question in term['unknown_if_missing'] + term['unknown_if_nonpositive']:
                value, failure = self._number(question)
                values.append(failure or _Truth(None if value is None else True))
                unknown_guard |= value is None
            for question in term['unknown_if_nonpositive']:
                value, failure = self._number(question)
                if value is not None and value <= 0:
                    unknown_guard = True
            result = _reduce(values, len(values))
            return _Truth(reasons=('rule_guard_unknown',)) if unknown_guard and not result.system else result
        if kind == 'coded_result':
            return self._answer(term['question'], term['answer'])
        if kind in ('finding', 'genomic_subject'):
            return self._genomic(term)
        if kind == 'assertion':
            return self._assertion(term['concept'])
        if kind == 'condition':
            if {'qualifiers', 'index_disease', 'association'} & term.keys():
                return _system('unsupported', 'condition_context_binding_missing')
            return self._assertion(term['concept'])
        if kind == 'transplant_history':
            return _system('unsupported', 'sct_relation_binding_missing')
        if kind == 'concomitant_treatment':
            return _system('unsupported', 'medication_context_binding_missing')
        return _system('unsupported', 'term_not_supported')

    def _assertion(self, concept):
        table = _TABLES.get(concept['domain_id'])
        if table is None:
            return _system('unsupported', 'concept_domain_binding_missing')
        facts = [fact for fact in self.physical if fact['table'] == table
                 and fact['concept_id'] == concept['concept_id'] and 'parent' not in fact]
        if any(fact['state'] == 'present' for fact in facts):
            return _Truth(True)
        if facts and all(fact['state'] == 'negative' for fact in facts):
            return _Truth(False)
        # Even a complete field-level summary lacks per-target negative coverage.
        return _Truth(reasons=('target_assertion_missing',))

    def _question_facts(self, question, facts=None):
        return [fact for fact in (self.physical if facts is None else facts)
                if fact['table'] == question['omop_table']
                and fact['concept_id'] == question['concept_id']
                and fact['source_value'] == question['source_value']]

    @staticmethod
    def _operand_state(facts):
        # V1 does not say whether this state denies the observation/subject or
        # describes its negative clinical result. Do not guess the polarity.
        if any(fact['state'] in ('negative', 'asserted_none') for fact in facts):
            return _system('unsupported', 'physical_state_binding_missing')
        return None

    def _value(self, question, facts=None):
        values = []
        column = {'number': 'value_as_number', 'string': 'value_as_string',
                  'concept': 'value_as_concept_id'}.get(question['value_kind'])
        if column is None:
            return None, _system('unsupported', 'question_kind_not_supported')
        for fact in self._question_facts(question, facts):
            if fact['state'] in ('cleared', 'unanswered', 'indeterminate'):
                continue
            state_failure = self._operand_state((fact,))
            if state_failure is not None:
                return None, state_failure
            answer = fact['answer']
            nonnull = {key for key in ('value_as_number', 'value_as_string', 'value_as_concept_id')
                       if answer[key] is not None}
            # Concept writer retains the literal display in value_as_string.
            allowed = {column, 'value_as_string'} if column == 'value_as_concept_id' else {column}
            if nonnull - allowed:
                return None, _system('unavailable', 'physical_answer_kind_conflict')
            if column == 'value_as_concept_id':
                if answer[column] == '0' or (answer[column] is None
                                             and answer['value_as_string'] not in (None, '')):
                    return None, _system('unavailable', 'physical_answer_unmapped')
            if answer[column] is not None:
                if question['unit'] != answer['unit_source_value']:
                    return None, _system('unavailable', 'physical_answer_unit_conflict')
                values.append(answer[column])
        if len(set(values)) > 1:
            return None, _Truth(reasons=('selected_answers_conflict',))
        return (values[0] if values else None), None

    def _number(self, question):
        return self._value(question)

    def _answer(self, question, answer, facts=None):
        value, failure = self._value(question, facts)
        if failure is not None:
            return failure
        if value is None:
            return _Truth(reasons=('physical_answer_missing',))
        if answer['kind'] == 'concept':
            return _Truth(value == answer['concept']['concept_id'])
        if answer['match'] == 'comma_joined_member':
            return _Truth(answer['value'] in [item.strip() for item in value.split(',')])
        return _Truth(value == answer['value'])

    def _genomic(self, term):
        feature = term['feature']
        table = _TABLES.get(feature['domain_id'])
        if table is None:
            return _system('unsupported', 'genomic_feature_binding_missing')
        subjects = [fact for fact in self.physical if fact['table'] == table
                    and fact['concept_id'] == feature['concept_id']
                    and fact['state'] not in ('unanswered', 'cleared', 'indeterminate')]
        results = []
        for subject in subjects:
            parent_id = subject.get('parent', {}).get('row_id')
            if parent_id is None:
                if subject['table'] != 'measurement':
                    return _system('unsupported', 'genomic_parent_binding_missing')
                parent_id = subject['row_id']
            parent = self.by_physical[('measurement', parent_id)]
            state_failure = self._operand_state((subject, parent))
            if state_failure is not None:
                results.append(state_failure)
                continue
            components = [fact for fact in self.physical
                          if fact.get('parent', {}).get('row_id') == parent_id]
            if term['kind'] == 'finding':
                status = term['status']
                value, failure = self._value(status['question'], components)
                results.append(failure or _Truth(True if value == 'present' else
                                                False if value == 'absent' else None))
            elif 'qualifier' in term:
                bound = term['qualifier']
                results.append(self._answer(bound['question'], bound['answer'], components))
            else:
                # Subject-only existence does not assert a positive result.
                results.append(_Truth(True))
        return _reduce(results, 1) if results else _Truth(reasons=('genomic_subject_missing',))
