"""A PROMOP record names the adequacy verdicts but has no column for them (#621).

CB's `PromopMatchingSurface._provided_fields` names hepatic and haematological
adequacy, so a patient without the supporting labs is UNDECIDED rather than
"not adequate". EXACT's PROMOP adapter dropped the nulls before provenance was
captured and then derived False, which reaches `not_matched` and removes the
trial from the patient's results.

The inline payload keeps its legacy behaviour: there, an unnamed aggregate is
still False, and only a caller that states null gets unknown (#5483).
"""
import pytest
from django.test import override_settings

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.querysets.trial import TrialQuerySet
from trials.models import Trial
from trials.services.patient_info.promop_adapter import (
    PROMOP_NAMED_NULL_FIELDS,
    build_patient_info_from_promop_row,
)
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.trial_details.trial_attributes import TrialAttributes

# Hepatic ULN norms are DB-backed in this repo, so the whole file is DB-bound.
pytestmark = pytest.mark.django_db

MM = 'multiple myeloma'
HEPATIC_LABS = dict(
    # PROMOP sends display titles; the adapter resolves them to codes.
    ethnicity='Caucasian/European', gender='M',
    liver_enzyme_levels_ast=40, liver_enzyme_levels_alt=45,
    serum_bilirubin_level_total='1.2',
)
HAEM_LABS = dict(
    absolute_neutrophile_count=2000, absolute_neutrophile_count_units='CELLS/UL',
    platelet_count=100000, platelet_count_units='CELLS/UL',
    hemoglobin_level=12, hemoglobin_level_units='G/DL',
)


def test_named_fields_match_cb_surface():
    assert PROMOP_NAMED_NULL_FIELDS == ('hepatic_adequacy_status', 'haematological_adequacy_status')


@pytest.mark.parametrize('attr', ['hepatic_adequacy_status', 'haematological_adequacy_status'])
def test_missing_labs_read_unknown_on_the_promop_path(attr):
    patient = build_patient_info_from_promop_row({'disease': MM})
    assert getattr(patient, attr) is None
    assert attr in patient._provided_fields


@pytest.mark.parametrize('attr,labs', [
    ('hepatic_adequacy_status', HEPATIC_LABS),
    ('haematological_adequacy_status', HAEM_LABS),
])
def test_supplied_labs_still_produce_a_definite_verdict(attr, labs):
    patient = build_patient_info_from_promop_row({'disease': MM, **labs})
    assert getattr(patient, attr) is True


@pytest.mark.parametrize('attr,labs', [
    ('hepatic_adequacy_status', dict(HEPATIC_LABS, liver_enzyme_levels_ast=120)),
    ('haematological_adequacy_status', {**HAEM_LABS, 'platelet_count': 50000}),
])
def test_abnormal_labs_are_a_definite_negative_even_on_promop(attr, labs):
    patient = build_patient_info_from_promop_row({'disease': MM, **labs})
    assert getattr(patient, attr) is False


def test_inline_payload_without_the_aggregate_keeps_legacy_false():
    """Only the PROMOP source names the field; inline does not (#5483)."""
    patient = _build_in_memory({'disease': MM})
    assert patient.hepatic_adequacy_status is False
    assert patient.haematological_adequacy_status is False


def test_inline_payload_can_still_name_unknown_explicitly():
    patient = _build_in_memory({'disease': MM, 'hepatic_adequacy_status': None})
    assert patient.hepatic_adequacy_status is None


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('attr,required_field', [
    ('hepatic_adequacy_status', 'hepatic_adequacy_required'),
    ('haematological_adequacy_status', 'haematological_adequacy_required'),
])
def test_missing_labs_keep_a_requiring_trial_potential(attr, required_field):
    patient = build_patient_info_from_promop_row({'disease': MM})
    trial = Trial.objects.create(disease=MM, **{required_field: True})
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(attr) == 'unknown'
    pool, traces = Trial.objects.filter_by_patient_info(patient, add_traces=True)
    assert pool.filter(pk=trial.pk).exists()
    # A blank attribute is skipped in the SQL loop, so nothing is traced for it.
    assert attr not in [row['attr'] for row in traces]


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_inline_missing_labs_still_reject_a_requiring_trial():
    """Guard the legacy contract: only the PROMOP source becomes undecided."""
    patient = _build_in_memory({'disease': MM})
    trial = Trial.objects.create(disease=MM, haematological_adequacy_required=True)
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(
        'haematological_adequacy_status') == 'not_matched'
    pool, _ = Trial.objects.filter_by_patient_info(patient)
    assert not pool.filter(pk=trial.pk).exists()


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_trial_details_show_the_criterion_as_unknown_not_failed():
    patient = build_patient_info_from_promop_row({'disease': MM})
    trial = Trial.objects.create(
        disease=MM, hepatic_adequacy_required=True,
    )
    details = TrialAttributes(trial, patient).get_user_details(subform_attrs={})
    assert details['hepatic_adequacy_required']['uvalue'] is None
