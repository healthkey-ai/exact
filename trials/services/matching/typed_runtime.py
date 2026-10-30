"""Native transport/presentation of package results; NOT another clinical engine.

There is deliberately no production resolver yet. #662 must supply verified
publication/evidence and person/scope binding. The flag defaults off; enabling
it without a server-owned resolver fails closed rather than running legacy.
"""
from collections import Counter

from django.conf import settings
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException

from exact_matching.contracts import ContractError
from exact_matching.request_evaluation import RequestEvaluation, RESULT_SCHEMA
from exact_matching.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
from exact_matching.trial_details.configs import SUBFORM_ATTRS_MAPPING
from trials.services.attribute_names import AttributeNames


class TypedMatchingUnavailable(APIException):
    status_code = 503
    default_detail = 'Typed matching context is unavailable.'
    default_code = 'typed_matching_unavailable'


def resolve_evaluation(request, patient_info):
    if not getattr(settings, 'EXACT_TYPED_MATCHING_ENABLED', False):
        return None
    store = getattr(request, '_request', request)
    holder = getattr(store, '_exact_typed_evaluation', None)
    if holder is not None:
        if isinstance(holder[0], APIException):
            raise holder[0]
        if not holder[0].is_bound_to(patient_info):
            failure = TypedMatchingUnavailable({'code': 'request_patient_binding'})
            store._exact_typed_evaluation = (failure,)
            raise failure
        return holder[0]
    try:
        resolver = getattr(settings, 'EXACT_TYPED_MATCHING_RESOLVER', None)
        if not resolver or patient_info is None:
            raise ContractError('trusted_context_unavailable')
        if isinstance(resolver, str):
            try:
                resolver = import_string(resolver)
            except (ImportError, AttributeError) as error:
                raise ContractError('trusted_resolver_configuration') from error
        if not callable(resolver):
            raise ContractError('trusted_resolver_configuration')
        evaluation = resolver(request, patient_info)
        if not isinstance(evaluation, RequestEvaluation) or not evaluation.is_bound_to(patient_info):
            raise ContractError('request_patient_binding')
    except ContractError as error:
        failure = TypedMatchingUnavailable({'code': str(error)})
        store._exact_typed_evaluation = (failure,)
        raise failure from error
    store._exact_typed_evaluation = (evaluation,)
    return evaluation


def evaluate(evaluation, trials):
    try:
        return evaluation.evaluate(trials)
    except ContractError as error:
        raise TypedMatchingUnavailable({'code': str(error)}) from error


def attribute_counts(trials, evaluation):
    return dict(Counter(attr for row in trials for attr in evaluation.result_for(row.pk).missing_attributes))


def missing_attributes(result, counts):
    """Same host keys, only answerable unknowns. No system row becomes a question."""
    return [{'trialAttributeName': AttributeNames.get_by_snake_case(
                 (USER_TO_TRIAL_ATTRS_MAPPING[name]['attr'][0]
                  if isinstance(USER_TO_TRIAL_ATTRS_MAPPING[name]['attr'], list)
                  else USER_TO_TRIAL_ATTRS_MAPPING[name]['attr'])),
             'userAttributeName': AttributeNames.get_by_snake_case(name),
             'userAttributeTitle': AttributeNames.humanize(AttributeNames.get_by_snake_case(name)),
             'count': counts.get(name, 0)} for name in result.missing_attributes]


def details(evaluation, trial, template=None):
    """Both native templates avoid legacy typed reads and select/empty suppression.

    Source metadata is resolved by patient attribute first, field-keyed fallback
    second. No new code-to-concept conversion or display-value clinical inference.
    This versioned payload carries no guessed patient values for native operands.
    """
    result = evaluation.result_for(trial.pk)
    predicates = {item['field']: item for item in evaluation.predicates_for(trial.pk)}
    fields = {item.field: item for item in result.criteria}
    rows = []
    for item in result.attributes:
        meta = USER_TO_TRIAL_ATTRS_MAPPING[item.attribute]
        sources = SUBFORM_ATTRS_MAPPING.get(item.attribute)
        if sources is None:
            sources = next((SUBFORM_ATTRS_MAPPING[name] for name in item.fields
                            if name in SUBFORM_ATTRS_MAPPING), meta.get('attrs_to_compute', ()))
        if callable(sources):
            sources = sources(evaluation.patient_info)
        sources = tuple(sources)
        if item.fields:
            requirement = {name: predicates[name]['native'] for name in item.fields}
        else:
            names = meta['attr'] if isinstance(meta['attr'], list) else [meta['attr']]
            # Present the existing scalar bounds without reading/recomputing a
            # migrated patient value or invoking a second matcher.
            if meta['type'] in ('min_value', 'max_value', 'min_max_value'):
                names = [meta.get('attr_' + bound, str(meta['attr']) + '_' + bound)
                         for bound in ('min', 'max') if meta['type'] == 'min_max_value'
                         or meta['type'].startswith(bound)]
            requirement = {name: getattr(trial, name, None) for name in names}
        rows.append({'name': AttributeNames.get_by_snake_case(item.attribute),
                     'ufield': AttributeNames.get_by_snake_case(item.attribute),
                     'label': AttributeNames.humanize(AttributeNames.get_by_snake_case(item.attribute)),
                     'type': 'json', 'ureadonly': bool(meta.get('is_computed_value') or sources),
                     'uvalue': None, 'utype': 'json', 'uoptions': None,
                     'value': requirement,
                     'matchingType': {'satisfied': 'matched', 'unconstrained': 'matched',
                                      'violated': 'not_matched', 'patient_unknown': 'unknown'}.get(item.status, item.status),
                     'evaluationStatus': item.status, 'reasons': list(item.reasons),
                     'dependencies': list(sources),
                     'subform_details': [{'name': AttributeNames.get_by_snake_case(name),
                                          'label': AttributeNames.humanize(AttributeNames.get_by_snake_case(name))}
                                         for name in sources],
                     'criteria': [{'field': name, 'status': fields[name].status,
                                   'reasons': list(fields[name].reasons)} for name in item.fields]})
    general = [{'name': 'matchScore', 'type': 'string', 'ureadonly': True,
                'value': result.score, 'uvalue': result.score, 'matchingType': result.status}]
    output = {'general': general, 'trialEligibilityAttributes': rows}
    if template == 'all_attributes_in_groups':
        # This is explicitly versioned native metadata, not legacy code labels.
        output['eligibility'] = rows
    return {'schema': RESULT_SCHEMA, 'details': output,
            'group_names': [{'value': 'eligibility', 'label': 'Eligibility'}]}
