"""Synthetic presentation values: no clinical approval or live typed activation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from trials.models import Trial
from trials.services.trial_details.supplied_presentation import SuppliedTrialPresentation
from trials.services.trial_details.trial_templates import TrialTemplates

# EXACT's session fixture seeds reference data through the database, so every
# test in this module needs database access even when the assertion itself does not.
pytestmark = pytest.mark.django_db


class HostPatient(SimpleNamespace):
    """A host surface that declares the optional persisted-derived schema.

    EXACT ships no `PatientInfo` model, so the host owns this list. These are the
    fields CB's `PatientInfo` derives unconditionally, plus the three it only
    assigns when derivation succeeds.
    """

    DERIVED_FIELDS_IN_SAVE = (
        'bmi', 'meets_crab', 'meets_slim', 'progression',
        'stem_cell_transplant_history', 'renal_adequacy_status',
        'hepatic_adequacy_status', 'haematological_adequacy_status',
        'estimated_glomerular_filtration_rate', 'hr_status', 'tnbc_status',
        'tp53_disruption', 'mipi_risk', 'mipi_c_risk',
    )


def views():
    general = [{'name': 'matchScore', 'label': 'Match Score', 'type': 'string',
                'value': None, 'uvalue': None, 'ureadonly': True}]
    rows = [
        {'name': 'her2StatusesRequired', 'label': 'HER2', 'ufield': 'her2Status',
         'type': 'select', 'options': [], 'value': None, 'uvalue': False,
         'ureadonly': False, 'matchingType': 'unavailable'},
        {'name': 'measurableDiseaseImwgRequired', 'label': 'Measurable Disease',
         'ufield': 'measurableDiseaseImwg', 'type': 'boolean', 'value': True,
         'uvalue': None, 'ureadonly': True, 'matchingType': 'unknown',
         'subform_details': [
             {'name': 'monoclonalProteinSerum', 'type': 'float', 'value': 0},
             {'name': 'monoclonalProteinUrine', 'type': 'float', 'value': None},
         ]},
        {'name': 'therapyTypesRequired', 'label': 'Therapy Types', 'ufield': 'firstLineTherapy',
         'type': 'select', 'options': [], 'value': [], 'uvalue': None,
         'ureadonly': False, 'matchingType': 'unsupported'},
    ]
    return {'potential': {'general': general, 'trialEligibilityAttributes': rows},
            'grouped': {'general': deepcopy(general), 'eligibility': deepcopy(rows)},
            'group_names': [{'value': 'eligibility', 'label': 'Eligibility'}]}


def presentation(patient, payload=None, trial_id=7):
    return SuppliedTrialPresentation(trial_id, patient, **(payload if payload is not None else views()))


@pytest.fixture
def patient():
    return HostPatient(name='synthetic')


@pytest.fixture
def no_legacy(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('supplied presentation reached legacy patient/value/matcher reads')

    monkeypatch.setattr('trials.services.trial_details.trial_templates.TrialAttributes', fail)
    monkeypatch.setattr('trials.services.trial_details.trial_templates.UserToTrialAttrMatcher', fail)
    monkeypatch.setattr('trials.services.trial_details.trial_attributes.ValueOptions', fail)


@pytest.mark.django_db
def test_both_views_and_model_delegate_bypass_all_legacy_reads(no_legacy, patient):
    value = presentation(patient)
    trial = SimpleNamespace(pk=7)
    service = TrialTemplates(trial, patient_info=patient, presentation=value)
    assert service.potential_attributes_first_view([]) == views()['potential']
    assert service.all_attributes_in_groups_view() == views()['grouped']
    assert service.group_names() == views()['group_names']
    for template, key in ((None, 'potential'), ('all_attributes_in_groups', 'grouped')):
        response = Trial.details_and_group_names(
            trial, patient, template, ['ignored'], presentation=value)
        assert response == {'details': views()[key], 'group_names': views()['group_names']}


@pytest.mark.django_db
def test_saved_trial_model_delegates_supplied_views(no_legacy, patient):
    from tests.factories import TrialFactory
    trial = TrialFactory()
    value = presentation(patient, trial_id=trial.pk)
    response = trial.details_and_group_names(patient, None, [], presentation=value)
    assert response == {'details': views()['potential'], 'group_names': views()['group_names']}


def test_system_rows_empty_selects_false_zero_and_subforms_are_not_reclassified(no_legacy, patient):
    service = TrialTemplates(SimpleNamespace(pk=7), patient_info=patient,
                             presentation=presentation(patient))
    response = service.potential_attributes_first_view({'pretend_missing': 99})
    rows = response['trialEligibilityAttributes']
    assert [row['matchingType'] for row in rows] == ['unavailable', 'unknown', 'unsupported']
    assert rows[0]['uvalue'] is False and rows[0]['value'] is None
    assert rows[2]['options'] == [] and rows[2]['value'] == []
    assert rows[1]['ureadonly'] is True
    assert rows[1]['subform_details'][0]['value'] == 0


def test_input_and_each_returned_view_are_independent_copies(no_legacy, patient):
    payload = views()
    value = presentation(patient, payload)
    payload['potential']['trialEligibilityAttributes'][0]['matchingType'] = 'matched'
    payload['grouped'].clear()
    payload['group_names'][0]['label'] = 'mutated'
    service = TrialTemplates(SimpleNamespace(pk=7), patient_info=patient, presentation=value)
    result = service.potential_attributes_first_view(None)
    result['trialEligibilityAttributes'][0]['matchingType'] = 'matched'
    result['trialEligibilityAttributes'][1]['subform_details'].clear()
    assert service.potential_attributes_first_view(None) == views()['potential']
    assert service.all_attributes_in_groups_view() == views()['grouped']
    names = service.group_names()
    names.clear()
    assert service.group_names() == views()['group_names']


def test_explicit_empty_snapshot_is_not_absence_or_a_legacy_fallback(no_legacy, patient):
    payload = {'potential': {'general': [], 'trialEligibilityAttributes': []},
               'grouped': {}, 'group_names': []}
    service = TrialTemplates(SimpleNamespace(pk=7), patient_info=patient,
                             presentation=presentation(patient, payload))
    assert service.potential_attributes_first_view(None) == payload['potential']
    assert service.all_attributes_in_groups_view() == {}
    assert service.group_names() == []


@pytest.mark.parametrize('trial_id', [8, None, True])
def test_binding_to_the_trial_is_checked_before_legacy_reads(no_legacy, patient, trial_id):
    value = presentation(patient)
    with pytest.raises(ValueError, match='presentation_binding'):
        TrialTemplates(SimpleNamespace(pk=trial_id), patient_info=patient, presentation=value)


@pytest.mark.parametrize('other_patient', [object(), None])
def test_binding_to_the_exact_patient_object_is_checked(no_legacy, patient, other_patient):
    value = presentation(patient)
    with pytest.raises(ValueError, match='presentation_binding'):
        TrialTemplates(SimpleNamespace(pk=7), patient_info=other_patient, presentation=value)


@pytest.mark.parametrize('value', [False, {}, [], 'a presentation'])
def test_invalid_supplied_value_never_falls_back(no_legacy, value):
    with pytest.raises(ValueError, match='presentation_type'):
        TrialTemplates(SimpleNamespace(pk=7), patient_info=object(), presentation=value)


@pytest.mark.parametrize('trial_id', [None, 0, True])
def test_unsaved_or_unbound_snapshots_are_rejected(trial_id, patient):
    with pytest.raises(ValueError, match='presentation_binding'):
        presentation(patient, trial_id=trial_id)


def test_supplied_patient_snapshot_cannot_be_used_for_lazy_metadata(no_legacy, patient):
    service = TrialTemplates(SimpleNamespace(pk=7), patient_info=patient,
                             presentation=presentation(patient))
    with pytest.raises(ValueError, match='supplied_presentation_metadata'):
        service.get_field_name(object())


def test_none_keeps_the_exact_legacy_constructor_and_model_forwarding(monkeypatch):
    calls = []
    sentinel = object()

    def legacy_attributes(trial, patient_info=None):
        calls.append((trial, patient_info))
        return sentinel

    monkeypatch.setattr('trials.services.trial_details.trial_templates.TrialAttributes', legacy_attributes)
    trial, patient = object(), object()
    assert TrialTemplates(trial, patient_info=patient, presentation=None)._trial_attributes is sentinel
    assert calls == [(trial, patient)]

    class LegacyTemplates:
        def __init__(self, trial, patient_info=None):
            calls.append((trial, patient_info))

        def details_and_group_names(self, template, attrs):
            return template, attrs

    monkeypatch.setattr('trials.models.TrialTemplates', LegacyTemplates)
    assert Trial.details_and_group_names(trial, patient, 'old', []) == ('old', [])
    assert calls[-1] == (trial, patient)


@pytest.mark.parametrize('source', ['potential', 'grouped'])
@pytest.mark.parametrize('patient_field', ['measurableDiseaseImwg', None])
def test_computed_verdict_cannot_be_supplied_as_editable(source, patient_field, patient):
    payload = views()
    rows = payload['potential']['trialEligibilityAttributes'] if source == 'potential' else payload['grouped']['eligibility']
    rows[1]['ureadonly'] = False
    rows[1]['ufield'] = patient_field
    with pytest.raises(ValueError, match='presentation_computed_readonly'):
        presentation(patient, payload)


@pytest.mark.parametrize('trial_name', ['mipiRisk', 'hrStatusesRequired', 'tnbcStatus', 'tp53DisruptionRequired'])
def test_host_declared_derived_schema_is_protected(trial_name, patient):
    payload = views()
    payload['potential']['trialEligibilityAttributes'][0].update(name=trial_name, ufield=None, ureadonly=False)
    with pytest.raises(ValueError, match='presentation_computed_readonly'):
        presentation(patient, payload)


@pytest.mark.parametrize('trial_name,patient_field', [('meetsGELF', 'meetsGELF'), ('meetsLugano', 'meetsLugano')])
def test_manually_controlled_answers_remain_editable(trial_name, patient_field, patient):
    payload = views()
    payload['potential']['trialEligibilityAttributes'][0].update(
        name=trial_name, ufield=patient_field, ureadonly=False)
    assert presentation(patient, payload).view('potential') == payload['potential']


@pytest.mark.parametrize('nested', [False, True])
def test_persisted_derived_slim_cannot_be_unlocked_by_manual_control(nested, patient):
    payload = views()
    rows = payload['potential']['trialEligibilityAttributes']
    if nested:
        rows[1]['subform_details'].append({'name': 'meetsSLIM', 'value': False, 'readonly': False})
    else:
        rows[0].update(name='meetsSLIM', ufield='meetsSLIM', ureadonly=False)
    with pytest.raises(ValueError, match='presentation_computed_readonly'):
        presentation(patient, payload)


@pytest.mark.parametrize('name,patient_field', [
    ('diseaseProgressionActiveRequired', 'progression'),
    ('stemCellTransplantHistoryRequired', 'stemCellTransplantHistory'),
    ('estimatedGlomerularFiltrationRateMin', 'estimatedGlomerularFiltrationRate'),
])
def test_conditionally_derived_answers_keep_manual_fallback_metadata(name, patient_field, patient):
    payload = views()
    payload['potential']['trialEligibilityAttributes'][0].update(
        name=name, ufield=patient_field, ureadonly=False)
    assert presentation(patient, payload).view('potential') == payload['potential']
    payload['potential']['trialEligibilityAttributes'][0]['ureadonly'] = True
    assert presentation(patient, payload).view('potential') == payload['potential']


@pytest.mark.parametrize('name', ['progression', 'stemCellTransplantHistory',
                                  'estimatedGlomerularFiltrationRate'])
def test_nested_conditional_derived_stays_readonly(name, patient):
    payload = views()
    payload['potential']['trialEligibilityAttributes'][1]['subform_details'].append(
        {'name': name, 'value': None, 'readonly': False})
    with pytest.raises(ValueError, match='presentation_computed_readonly'):
        presentation(patient, payload)


@pytest.mark.parametrize('source', ['potential', 'grouped'])
@pytest.mark.parametrize('bound', ['min', 'max', 'range'])
@pytest.mark.parametrize('readonly', [False, None])
def test_calculated_uln_aliases_require_readonly_but_source_labs_stay_editable(
        source, bound, readonly, patient):
    from trials.services.attribute_names import AttributeNames
    from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING

    for patient_field, metadata in USER_TO_TRIAL_ATTRS_MAPPING.items():
        bound_key = 'uln_attr_' + ('min' if bound == 'range' else bound)
        if bound_key not in metadata:
            continue
        payload = views()
        rows = payload['potential']['trialEligibilityAttributes'] if source == 'potential' else payload['grouped']['eligibility']
        alias_name = metadata[bound_key]
        if bound == 'range':
            alias_name = alias_name.removesuffix('_min')
        row = rows[0]
        row.update(name=AttributeNames.get_by_snake_case(alias_name),
                   ufield=AttributeNames.get_by_snake_case(patient_field))
        if readonly is None:
            row.pop('ureadonly', None)
        else:
            row['ureadonly'] = readonly
        with pytest.raises(ValueError, match='presentation_computed_readonly'):
            presentation(patient, payload)
        row['ureadonly'] = True
        assert presentation(patient, payload).view(source) == payload[source]
        row.update(name=AttributeNames.get_by_snake_case(patient_field), ureadonly=False)
        assert presentation(patient, payload).view(source) == payload[source]


@pytest.mark.parametrize('source', ['potential', 'grouped'])
@pytest.mark.parametrize('status', [None, [], {}, 'eligible', 'invented'])
def test_invalid_status_is_a_stable_presentation_error(source, status, patient):
    payload = views()
    rows = payload['potential']['trialEligibilityAttributes'] if source == 'potential' else payload['grouped']['eligibility']
    rows[0]['matchingType'] = status
    with pytest.raises(ValueError, match='presentation_status'):
        presentation(patient, payload)


@pytest.mark.parametrize('mutation', ['potential', 'grouped', 'admin', 'groups', 'nonstring_key',
                                     'custom', 'nan', 'deep', 'wide', 'text', 'cycle', 'unicode', 'integer'])
def test_malformed_or_unbounded_data_never_gets_serialized_or_silently_accepted(mutation, patient):
    payload = views()
    if mutation == 'potential':
        payload['potential'].pop('general')
    elif mutation == 'grouped':
        payload['grouped'] = []
    elif mutation == 'admin':
        payload['grouped']['admin'] = []
    elif mutation == 'groups':
        payload['group_names'].append(payload['group_names'][0])
    elif mutation == 'nonstring_key':
        payload['potential'][1] = []
    elif mutation == 'custom':
        class MustNotConvert:
            def __deepcopy__(self, memo):
                pytest.fail('must validate before deepcopy')

            def __str__(self):
                pytest.fail('must not coerce custom object')
        payload['potential']['general'][0]['uvalue'] = MustNotConvert()
    elif mutation == 'nan':
        payload['potential']['general'][0]['uvalue'] = float('nan')
    elif mutation == 'deep':
        nested = []
        payload['potential']['general'][0]['uvalue'] = nested
        for _ in range(40):
            child = []
            nested.append(child)
            nested = child
    elif mutation == 'wide':
        payload['potential']['general'][0]['uvalue'] = [None] * 50001
    elif mutation == 'text':
        payload['potential']['general'][0]['uvalue'] = 'x' * 1_000_001
    elif mutation == 'cycle':
        payload['potential']['general'].append(payload['potential'])
    elif mutation == 'unicode':
        payload['potential']['general'][0]['uvalue'] = '\ud800'
    elif mutation == 'integer':
        payload['potential']['general'][0]['uvalue'] = 1 << 10000
    with pytest.raises(ValueError, match='presentation_'):
        presentation(patient, payload)


@pytest.mark.parametrize('value', [[1 << 4095] * 2000, '\x00' * 200_000, '\\' * 600_000])
def test_aggregate_payload_budget_is_checked_before_full_serialization(monkeypatch, value, patient):
    from trials.services.trial_details import supplied_presentation as module

    payload = views()
    payload['potential']['general'][0]['uvalue'] = value

    def must_not_serialize(*args, **kwargs):
        pytest.fail('oversized payload reached full JSON serialization')

    monkeypatch.setattr(module.json, 'dumps', must_not_serialize)
    with pytest.raises(ValueError, match='presentation_budget'):
        presentation(patient, payload)


@pytest.mark.parametrize('value', [False, None, 0, -20, 1.1, 1e100, '\x00\b\n"\\é🙂', {'mixed': [1, 'x']}])
def test_preflight_byte_budget_accepts_exact_compact_json_size(monkeypatch, value):
    import json
    from trials.services.trial_details import supplied_presentation as module

    expected = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(',', ':'))
    size = len(expected.encode('utf-8'))
    monkeypatch.setattr(module, '_MAX_BYTES', size)
    assert module._json_snapshot(value) == expected
    monkeypatch.setattr(module, '_MAX_BYTES', size - 1)
    with pytest.raises(ValueError, match='presentation_budget'):
        module._json_snapshot(value)


def test_snapshot_repr_does_not_disclose_patient_or_payload():
    payload = views()
    payload['potential']['general'][0]['uvalue'] = 'synthetic-secret-patient-text'
    patient = HostPatient(name='synthetic-patient-identity')
    value = presentation(patient, payload)
    assert 'synthetic-secret-patient-text' not in repr(value)
    assert 'synthetic-patient-identity' not in repr(value)


def test_host_without_declared_schema_uses_only_the_mapping_policy():
    """A surface that declares nothing gets mapping-declared protection only.

    CB's `PI_ATTR_READONLY` is CB UI metadata (its `mipi_risk` mapping carries no
    `is_computed_value` flag; the registry exists because the mapping is
    incomplete). EXACT ships no `PatientInfo` model, so it does not duplicate that
    UI registry: a host surface that wants those fields protected declares them
    in `DERIVED_FIELDS_IN_SAVE`. Mapping-declared computed values are still
    enforced without any schema declaration.
    """
    payload = views()
    payload['potential']['trialEligibilityAttributes'][0].update(
        name='meetsCrab', ufield=None, ureadonly=False)
    with pytest.raises(ValueError, match='presentation_computed_readonly'):
        presentation(SimpleNamespace(name='no-schema'), payload)

    payload = views()
    payload['potential']['trialEligibilityAttributes'][0].update(
        name='mipiRisk', ufield=None, ureadonly=False)
    assert presentation(SimpleNamespace(name='no-schema'), payload).view('potential') == payload['potential']
