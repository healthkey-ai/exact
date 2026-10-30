"""Bounded request orchestration, not evidence authentication (#661 / #662).

The host supplies a batch publication reader, never raw request JSON or inferred
wrappers around live omop_* cells. #662 must authenticate person, scope, selection
and publication. This module reuses ClaimsEvaluation and the existing scalar
matcher; it does not export evidence, expand concepts or adapt PatientRecord.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from itertools import islice

from django.core.serializers.json import DjangoJSONEncoder

from .contracts import ContractError
from .contracts.inventory import CRITERIA, MCL_GROUP
from .contracts.validation import canonical
from .typed_reader import ClaimsEvaluation, CriterionResult
from .utils import disease_attr_applies

RESULT_SCHEMA = 'exact-matching-result-1'
_FIELDS = frozenset(spec.field for spec in CRITERIA)
_SYSTEM = frozenset(('unavailable', 'unsupported'))
_BATCH_BYTES = 16_000_000


@dataclass(frozen=True)
class CandidateIdentity:
    trial_id: int
    row_sha256: str


@dataclass(frozen=True)
class TrialPredicates:
    """Host batch-reader response; row identity is a claim, not certification."""
    row_sha256: str
    predicates: tuple


@dataclass(frozen=True)
class AttributeResult:
    attribute: str
    status: str
    fields: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    known_violation: bool = False


@dataclass(frozen=True)
class TrialResult:
    trial_id: int
    context_id: str
    status: str
    score: int | None
    attributes: tuple[AttributeResult, ...]
    criteria: tuple[CriterionResult, ...]

    @property
    def missing_attributes(self):
        return tuple(item.attribute for item in self.attributes if item.status == 'patient_unknown')

    @property
    def system_issues(self):
        return tuple(item for item in self.attributes if item.status in _SYSTEM)

    def summary(self):
        return {'schema': RESULT_SCHEMA, 'contextId': self.context_id,
                'matchingType': self.status, 'matchScore': self.score,
                'attributesToFillIn': list(self.missing_attributes),
                'systemIssues': [{'attribute': item.attribute, 'status': item.status,
                                  'reasons': list(item.reasons)} for item in self.system_issues]}


def _groups():
    """Use current score units, not one score unit per Trial field.

    In particular the Python reference counts all four therapy-line attributes
    (even those skip_in_counts in SQL), and one unit for the four genomic axes.
    Fail on config drift rather than silently dropping a typed field.
    """
    from .patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
    groups = {}
    for attr, meta in USER_TO_TRIAL_ATTRS_MAPPING.items():
        fields = meta['attr']
        fields = (fields,) if isinstance(fields, str) else fields
        selected = tuple(field for field in fields if field in _FIELDS)
        if selected:
            groups[attr] = selected
    if set().union(*map(set, groups.values())) != _FIELDS:
        raise ContractError('score_inventory')
    groups['day_since_stem_cell_transplant'] = ('stem_cell_transplant_history_excluded',)
    return groups


def _combine(attr, results, fields):
    statuses = {item.status for item in results}
    reasons = tuple(sorted({reason for item in results for reason in item.reasons}))
    violation = ('violated' in statuses or 'known_exclusion_violation' in reasons)
    status = next((s for s in ('unsupported', 'unavailable') if s in statuses), None)
    if status is None:
        status = ('violated' if violation else 'patient_unknown' if 'patient_unknown' in statuses
                  else 'satisfied')
    # Retain both unavailable and unsupported, not just their chosen headline.
    reasons = tuple(sorted({*reasons, *('system_' + s for s in statuses if s in _SYSTEM)}))
    return AttributeResult(attr, status, fields, reasons, violation)


def _sct_contribution(attr, predicate, result):
    companions = predicate['source_companions']
    if attr == 'day_since_stem_cell_transplant':
        constrained = companions['day_since_stem_cell_transplant_required'] is not None
    else:
        constrained = (bool(companions['stem_cell_transplant_history_required']) or
                       bool(predicate['native'] and predicate['native'][0]['terms']))
    if predicate['state'] == 'blocked':
        return result
    if not constrained:
        return CriterionResult(result.field, 'unconstrained')
    if result.status in _SYSTEM:
        return result
    # V1's SCT reader is unsupported. A later supported aggregate still needs
    # explicit history/day contribution binding before it can score two units.
    return CriterionResult(result.field, 'unsupported', ('sct_contribution_binding_missing',))


def _row_identity(trial):
    if trial.get_deferred_fields():
        raise ContractError('deferred_trial_snapshot')
    try:
        values = {field.attname: getattr(trial, field.attname) for field in trial._meta.concrete_fields}
        encoded = json.dumps(values, cls=DjangoJSONEncoder, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ContractError('trial_snapshot') from error
    if len(encoded.encode()) > 1_000_000:
        raise ContractError('trial_snapshot_budget')
    return CandidateIdentity(trial.pk, hashlib.sha256(encoded.encode()).hexdigest())


class RequestEvaluation:
    """One fixed patient/context and bounded batch loader for one host request.

    batch_reader receives (CandidateIdentity, ...) and must return exactly
    {trial_id: TrialPredicates(row_sha256, predicates)}. It must enforce its own
    transport budget before downloading data; the consumer also checks its bounds.
    Structural checks here do NOT authenticate the returned publication claims.
    """

    def __init__(self, context, evidence, patient_info, batch_reader, *, max_trials=200):
        if type(max_trials) is not int or not 1 <= max_trials <= 10000:
            raise ContractError('candidate_budget')
        if patient_info is None or not callable(batch_reader):
            raise ContractError('request_inputs')
        self._reader = ClaimsEvaluation(context, evidence, max_evaluations=max_trials * len(_FIELDS))
        self.context_id = context.context_id
        self._source_patient = patient_info
        try:
            self.patient_info = deepcopy(patient_info)
        except Exception as error:
            raise ContractError('patient_snapshot') from error
        self._batch_reader = batch_reader
        self._max_trials = max_trials
        self._groups = _groups()
        self._identities = {}
        self._predicates = {}
        self._results = {}
        self._batch_bytes = 0

    def is_bound_to(self, patient_info):
        return self._source_patient is patient_info

    def materialize(self, trials):
        if hasattr(trials, 'query'):
            if trials.query.is_sliced:
                raise ContractError('sliced_candidates')
            # The extra row detects overflow, never forms a partial result.
            rows = tuple(trials[:self._max_trials + 1])
            if len(rows) > self._max_trials:
                raise ContractError('candidate_budget')
            return rows
        iterator = trials.iterator(chunk_size=100) if hasattr(trials, 'iterator') else iter(trials)
        rows = tuple(islice(iterator, self._max_trials + 1))
        if len(rows) > self._max_trials:
            raise ContractError('candidate_budget')
        return rows

    def evaluate(self, trials):
        # Read cap+1 only to detect overflow. No prefix can become a final page.
        rows, identities = [], {}
        for trial in self.materialize(trials):
            if len(rows) >= self._max_trials:
                raise ContractError('candidate_budget')
            identity = _row_identity(trial)
            if identity.trial_id in identities:
                raise ContractError('duplicate_candidate')
            if (identity.trial_id in self._identities and
                    self._identities[identity.trial_id] != identity):
                raise ContractError('trial_snapshot_changed')
            identities[identity.trial_id] = identity
            rows.append(trial)
        new = tuple(item for pk, item in identities.items() if pk not in self._results)
        if len(self._results) + len(new) > self._max_trials:
            raise ContractError('candidate_budget')
        if new:
            batch = self._batch_reader(new)
            if not isinstance(batch, dict) or set(batch) != {item.trial_id for item in new}:
                raise ContractError('candidate_publication_inventory')
            prepared = {}
            added_bytes = 0
            for identity in new:
                supplied = batch[identity.trial_id]
                if not isinstance(supplied, TrialPredicates) or supplied.row_sha256 != identity.row_sha256:
                    raise ContractError('candidate_publication_binding')
                if type(supplied.predicates) not in (tuple, list):
                    raise ContractError('predicate_inventory')
                encoded = canonical(list(supplied.predicates))
                added_bytes += len(encoded.encode())
                if self._batch_bytes + added_bytes > _BATCH_BYTES:
                    raise ContractError('predicate_batch_budget')
                documents = json.loads(encoded)
                if (len(documents) != len(_FIELDS) or not all(type(item) is dict for item in documents) or
                        {item.get('field') for item in documents} != _FIELDS):
                    raise ContractError('predicate_inventory')
                dependencies, companions = {}, {}
                for document in documents:
                    # Validate every field, including disease-inapplicable ones.
                    self._reader.evaluate(document)
                    for dependency in document['dependencies']:
                        key, value = dependency['field'], canonical(dependency)
                        if key in dependencies and dependencies[key] != value:
                            raise ContractError('predicate_dependency_snapshot')
                        dependencies[key] = value
                    for key, value in document['source_companions'].items():
                        value = canonical(value)
                        if key in companions and companions[key] != value:
                            raise ContractError('predicate_companion_snapshot')
                        companions[key] = value
                prepared[identity.trial_id] = encoded
            self._batch_bytes += added_bytes
            self._predicates.update(prepared)
            self._identities.update(identities)
            for row in rows:
                if row.pk not in self._results:
                    self._results[row.pk] = self._evaluate_trial(row)
        return tuple(self._results[row.pk] for row in rows)

    def predicates_for(self, trial_id):
        return json.loads(self._predicates[trial_id])

    def result_for(self, trial_id):
        return self._results[trial_id]

    def explanations(self, trial_id):
        result = self.result_for(trial_id)
        return [{'attr': item.attribute, 'status': item.status,
                 'fields': list(item.fields), 'reasons': list(item.reasons),
                 'knownViolation': item.known_violation}
                for item in result.attributes]

    def high_risk_breakdown(self, trial_id):
        result = self.result_for(trial_id)
        group = next((item for item in result.attributes if item.attribute == 'high_risk_mcl_criteria'), None)
        if group is None:
            return None
        return {'schema': RESULT_SCHEMA, 'status': group.status,
                'knownViolation': group.known_violation, 'reasons': list(group.reasons),
                'fields': [{'field': item.field, 'status': item.status, 'reasons': list(item.reasons)}
                           for item in result.criteria if item.field in MCL_GROUP]}

    def _evaluate_trial(self, trial):
        from .matcher import UserToTrialAttrMatcher
        documents = {item['field']: item for item in self.predicates_for(trial.pk)}
        fields = {field: self._reader.evaluate(item) for field, item in documents.items()}
        matcher = UserToTrialAttrMatcher(trial, self.patient_info)
        attributes = []
        for attr, meta in matcher.mapping.items():
            if 'disease' in meta and (matcher.disease_code is None or
                    not disease_attr_applies(meta['disease'], matcher.disease_code)):
                continue
            group = self._groups.get(attr)
            if group is None:
                status = matcher.attr_match_status(attr)
                attributes.append(AttributeResult(attr, {'matched': 'satisfied', 'not_matched': 'violated',
                                                         'unknown': 'patient_unknown'}[status],
                                                  known_violation=status == 'not_matched'))
                continue
            if group == MCL_GROUP:
                results = (self._reader.mcl_group([documents[name] for name in MCL_GROUP]),)
            elif attr in ('stem_cell_transplant_history', 'day_since_stem_cell_transplant'):
                name = 'stem_cell_transplant_history_excluded'
                results = (_sct_contribution(attr, documents[name], fields[name]),)
            else:
                results = tuple(fields[name] for name in group)
            attributes.append(_combine(attr, results, group))
        if any(item.known_violation for item in attributes):
            status, score = 'not_eligible', 0
        elif any(item.status in _SYSTEM for item in attributes):
            status = 'unsupported' if any(item.status == 'unsupported' for item in attributes) else 'unavailable'
            score = None
        else:
            score = int(100 * sum(item.status in ('satisfied', 'unconstrained') for item in attributes)
                        / len(attributes)) if attributes else 0
            status = 'potential' if any(item.status == 'patient_unknown' for item in attributes) else 'eligible'
        return TrialResult(trial.pk, self.context_id, status, score, tuple(attributes), tuple(fields.values()))


class EvaluatedTrials:
    """Final results, not a QuerySet. All selection precedes sorting and slicing."""
    def __init__(self, trials, evaluation, *, search_type=None):
        if search_type not in (None, '', 'eligible', 'potential', 'all', 'favorites', 'my_trials'):
            raise ContractError('search_mode')
        trials = evaluation.materialize(trials)
        results = evaluation.evaluate(trials)
        self.evaluation = evaluation
        for row, result in zip(trials, results):
            row.match_score = result.score
        self._rows = tuple(row for row, result in zip(trials, results) if
                           search_type in ('all', 'favorites', 'my_trials') or
                           result.status in _SYSTEM or
                           result.status == search_type or
                           (search_type not in ('eligible', 'potential') and result.status != 'not_eligible'))

    def __len__(self):
        return len(self._rows)

    def count(self):
        return len(self)

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, index):
        return self._rows[index]

    def ordered(self, key, *, reverse=False):
        # Clinical scores are never synthesized for unavailable rows. Nulls last
        # for every sort, stable primary-key tie breaker in either direction.
        result = object.__new__(type(self))
        result.evaluation = self.evaluation
        rows = sorted(self._rows, key=lambda row: row.pk)
        known = [row for row in rows if key(row) is not None]
        absent = [row for row in rows if key(row) is None]
        result._rows = tuple(sorted(known, key=key, reverse=reverse) + absent)
        return result
