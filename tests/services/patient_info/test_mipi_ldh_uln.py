"""Patient-specific LDH upper limit of normal feeds the MIPI scores (#631).

CB divides LDH by the patient/lab-specific ULN and falls back to the fixed 250
only when that value is unset or non-positive. Before this port EXACT always
divided by 250, so a patient with a high lab ULN was scored as more aggressive
than they are.
"""
from decimal import Decimal

import pytest
from django.test import override_settings

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import (
    MIPI_LDH_ULN,
    PatientInfoAttributes,
)
from exact_matching.querysets.trial import TrialQuerySet
from trials.models import Trial
from trials.services.patient_info.promop_adapter import normalize_promop_row
from trials.services.patient_info.resolve import _build_in_memory

MCL = 'mantle cell lymphoma'
# age 60, ECOG 1, WBC 7000/µL -> score 5.841 at ULN 250 (intermediate).
# ULN 400 -> 5.562 (low); ULN 100 -> 6.385 (high).
BASE = dict(
    disease=MCL, patient_age=60, ecog_performance_status=1,
    white_blood_cell_count=7000, white_blood_cell_count_units='CELLS/UL',
    lactate_dehydrogenase_level=300,
)


def _patient(**fields):
    return _build_in_memory(dict(BASE, **fields))


def _attrs(**fields):
    return PatientInfoAttributes(_patient(**fields))


def test_adapter_translates_the_promop_column():
    row = normalize_promop_row({'ldh_upper_limit_normal': 400})
    assert row['lactate_dehydrogenase_level_uln'] == 400


def test_zero_direct_uln_wins_in_the_adapter_and_falls_back_in_mipi():
    """A direct 0 is a value, not "missing", so it shadows the PROMOP column.

    The MIPI fallback then treats the non-positive ULN as unusable, exactly as
    CB does. PROMOP rows do not carry the direct key, so this only affects an
    inline caller that supplies both.
    """
    row = normalize_promop_row({
        'lactate_dehydrogenase_level_uln': 0, 'ldh_upper_limit_normal': 400,
    })
    assert row['lactate_dehydrogenase_level_uln'] == 0
    assert _attrs(lactate_dehydrogenase_level_uln=0).mipi_risk == 'intermediate'


def test_adapter_keeps_a_direct_uln_over_the_promop_column():
    row = normalize_promop_row({
        'lactate_dehydrogenase_level_uln': 300, 'ldh_upper_limit_normal': 400,
    })
    assert row['lactate_dehydrogenase_level_uln'] == 300


@pytest.mark.parametrize('uln', [400, '400', Decimal('400')])
def test_patient_specific_uln_lowers_the_score(uln):
    attrs = _attrs(lactate_dehydrogenase_level_uln=uln)
    assert attrs.mipi_risk == 'low'
    assert attrs.mipi_simplified_risk == 'intermediate'


def test_high_uln_can_raise_the_bucket():
    """A LOW ULN means a high LDH/ULN ratio, so the same LDH scores worse."""
    attrs = _attrs(lactate_dehydrogenase_level_uln=100)
    assert attrs.mipi_risk == 'high'
    assert attrs.mipi_simplified_risk == 'high'


@pytest.mark.parametrize('uln', [
    None, 0, -5, 'not a number',
    Decimal('1e-1000'),   # finite Decimal that underflows to 0.0
    Decimal('1e1000'),    # finite Decimal that cannot be a float
    '1e309',
])
def test_unusable_uln_falls_back_to_the_fixed_default(uln):
    attrs = _attrs(lactate_dehydrogenase_level_uln=uln)
    assert attrs.mipi_risk == 'intermediate'
    assert MIPI_LDH_ULN == 250


def test_missing_uln_key_behaves_like_the_fixed_default():
    assert _attrs().mipi_risk == 'intermediate'


@pytest.mark.parametrize('ldh,uln', [
    (Decimal('1e-100'), Decimal('1e308')),   # ratio underflows to 0.0
    (Decimal('1e308'), Decimal('1e-300')),   # ratio overflows to inf
])
def test_extreme_ratio_falls_back_instead_of_raising(ldh, uln):
    """log10(0) would raise ValueError during normalization."""
    attrs = _attrs(
        lactate_dehydrogenase_level=ldh, lactate_dehydrogenase_level_uln=uln,
    )
    assert attrs.mipi_risk in ('low', 'intermediate', 'high')
    assert attrs.mipi_simplified_risk in ('low', 'intermediate', 'high')


def test_mipi_c_follows_the_uln_through_mipi():
    patient = _patient(lactate_dehydrogenase_level_uln=400, ki67_proliferation_index=40)
    attrs = PatientInfoAttributes(patient)
    assert attrs.mipi_risk == 'low'
    assert attrs.mipi_c_risk == 'low_intermediate'
    assert PatientInfoAttributes(
        _patient(lactate_dehydrogenase_level_uln=None, ki67_proliferation_index=40)
    ).mipi_c_risk == 'high_intermediate'


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_trial_requiring_low_risk_matches_only_with_the_patient_uln():
    low = _patient(lactate_dehydrogenase_level_uln=400)
    fixed = _patient()
    trial = Trial.objects.create(disease=MCL, mipi_risks_required=['low'])
    assert UserToTrialAttrMatcher(trial, low).attr_match_status('mipi_risk') == 'matched'
    assert UserToTrialAttrMatcher(trial, fixed).attr_match_status('mipi_risk') == 'not_matched'
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(low)
    assert pool.exists()
    fixed_pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(fixed)
    assert not fixed_pool.exists()


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_high_risk_criteria_can_react_to_the_uln():
    """High MIPI is one of the named high-risk MCL criteria."""
    from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes as PIA

    high = _patient(lactate_dehydrogenase_level_uln=100)
    fixed = _patient()
    assert 'high_mipi' in PIA(high).high_risk_mcl_criteria
    assert 'high_mipi' not in (PIA(fixed).high_risk_mcl_criteria or '')
