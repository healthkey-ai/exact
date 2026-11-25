"""Canonical ANC/ALC sources instead of 1000x-low aliases (#628).

CB's `promop_surface._FIELD_MAPS` reads ANC and ALC from the canonical
thousands/µL columns on purpose: the direct CB-parity columns are either a
co-populated unscaled copy (ANC) or were filled without the ×1000 (ALC).
"""
from decimal import Decimal

import pytest
from django.test import override_settings
from rest_framework.renderers import JSONRenderer

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.querysets.trial import TrialQuerySet
from trials.models import Trial
from trials.services.patient_info.promop_adapter import (
    _CANONICAL_WINS,
    build_patient_info_from_promop_row,
    normalize_promop_row,
)
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.trial_details.trial_attributes import TrialAttributes

DISEASES = (
    'multiple myeloma', 'follicular lymphoma', 'breast cancer',
    'chronic lymphocytic leukemia', 'mantle cell lymphoma',
)
COUNT_SOURCES = tuple(
    case for case in _CANONICAL_WINS
    if case[0] in ('absolute_neutrophile_count', 'absolute_lymphocyte_count')
)


def _federated_builder(row):
    # Mirror the federation bridge: authenticated normalization, then the
    # serialized response is sent back as a strict inline payload.
    normalized = normalize_promop_row(dict(row))
    return _build_in_memory(_json(normalized), strict=True)


def _json(value):
    import json

    return json.loads(JSONRenderer().render(value))


BUILDERS = (build_patient_info_from_promop_row, _federated_builder)


@pytest.mark.parametrize('target,source,unit,scale', COUNT_SOURCES)
@pytest.mark.parametrize('canonical', [0, 2, '2', Decimal('2.5'), 4.5])
def test_canonical_count_wins_over_unscaled_alias(target, source, unit, scale, canonical):
    # The alias carries the same number unscaled, which is the defect.
    row = normalize_promop_row({target: canonical, source: canonical})
    assert row[target] == Decimal(str(canonical)) * scale
    if unit is not None:
        assert row[f'{target}_units'] == unit


@pytest.mark.parametrize('target,source,unit,scale', COUNT_SOURCES)
def test_canonical_count_only_source(target, source, unit, scale):
    row = normalize_promop_row({source: 3})
    assert row[target] == 3000
    if unit is not None:
        assert row[f'{target}_units'] == unit


@pytest.mark.parametrize('target,source,unit,scale', COUNT_SOURCES)
def test_canonical_count_is_idempotent(target, source, unit, scale):
    once = normalize_promop_row({target: 2, source: 2})
    assert normalize_promop_row(dict(once)) == once


@pytest.mark.parametrize('target,source,unit,scale', COUNT_SOURCES)
@pytest.mark.parametrize('blank', ['', '  '])
def test_blank_canonical_count_keeps_a_usable_direct_value(target, source, unit, scale, blank):
    row = normalize_promop_row({target: 2500, f'{target}_units': 'CELLS/UL', source: blank})
    assert row[target] == 2500
    if unit is not None:
        assert row[f'{target}_units'] == 'CELLS/UL'


@pytest.mark.parametrize('target,source,unit,scale', COUNT_SOURCES)
@pytest.mark.parametrize('malformed', ['NaN', 'abc', '1e309'])
def test_unusable_canonical_count_reads_unknown(target, source, unit, scale, malformed):
    """The alias may be the 1000x-low copy, so never publish it instead."""
    row = normalize_promop_row({target: 2, f'{target}_units': 'CELLS/UL', source: malformed})
    assert row[target] is None
    if unit is not None:
        assert row[f'{target}_units'] == unit


@pytest.mark.parametrize('target,source,unit,scale', COUNT_SOURCES)
def test_payload_without_canonical_count_is_unchanged(target, source, unit, scale):
    row = {target: 2500, f'{target}_units': 'CELLS/UL'}
    assert normalize_promop_row(dict(row)) == row


@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_anc_reaches_matcher_on_both_paths(disease, builder):
    patient = builder({
        'disease': disease,
        'absolute_neutrophile_count': 2, 'anc_thousand_per_ul': 2,
        'absolute_neutrophile_count_units': 'CELLS/UL',
    })
    assert patient.absolute_neutrophile_count == 2000
    assert PatientInfoAttributes(patient).get_value('absolute_neutrophile_count') == 2000


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_canonical_anc_agrees_across_pool_verdict_and_details(disease, builder):
    patient = builder({
        'disease': disease,
        'absolute_neutrophile_count': 2, 'anc_thousand_per_ul': 2,
        'absolute_neutrophile_count_units': 'CELLS/UL',
    })
    trial = Trial.objects.create(disease=disease, absolute_neutrophile_count_min=1500)
    assert UserToTrialAttrMatcher(
        trial, patient,
    ).attr_match_status('absolute_neutrophile_count') == 'matched'
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists()
    details = TrialAttributes(trial, patient).get_user_details(subform_attrs={})
    assert details['absolute_neutrophile_count_min']['uvalue'] == 2000


@pytest.mark.django_db
@pytest.mark.parametrize('builder', BUILDERS)
def test_canonical_anc_clears_a_trial_the_alias_would_have_excluded(builder):
    patient = builder({
        'disease': 'multiple myeloma',
        'absolute_neutrophile_count': 2, 'anc_thousand_per_ul': 2,
        'absolute_neutrophile_count_units': 'CELLS/UL',
    })
    trial = Trial.objects.create(disease='multiple myeloma', absolute_neutrophile_count_min=1500)
    # 2 cells/uL (the unscaled alias) is below every ANC cutoff; 2000 is not.
    assert patient.absolute_neutrophile_count != 2
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists()


@pytest.mark.parametrize('builder', BUILDERS)
def test_alc_canonical_source_reaches_the_dto(builder):
    patient = builder({
        'disease': 'chronic lymphocytic leukemia',
        'absolute_lymphocyte_count': 60, 'alc_thousand_per_ul': 60,
    })
    assert patient.absolute_lymphocyte_count == 60000
    assert PatientInfoAttributes(patient).get_value('absolute_lymphocyte_count') == 60000


@pytest.mark.django_db
@pytest.mark.parametrize('builder', BUILDERS)
def test_alc_canonical_source_changes_the_cll_verdict(builder):
    patient = builder({
        'disease': 'chronic lymphocytic leukemia',
        'absolute_lymphocyte_count': 5, 'alc_thousand_per_ul': 5,
    })
    trial = Trial.objects.create(
        disease='chronic lymphocytic leukemia', absolute_lymphocyte_count_min=5000,
    )
    assert UserToTrialAttrMatcher(
        trial, patient,
    ).attr_match_status('absolute_lymphocyte_count') == 'matched'
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists()


@pytest.mark.parametrize('builder', BUILDERS)
def test_canonical_anc_is_not_reported_as_missing(builder):
    from exact_matching.user_to_trial_attrs_mapper import UserToTrialAttrsMapper

    patient = builder({
        'disease': 'multiple myeloma',
        'anc_thousand_per_ul': 2, 'absolute_neutrophile_count_units': 'CELLS/UL',
    })
    assert 'absolute_neutrophile_count' not in UserToTrialAttrsMapper().potential_attrs_to_check(
        patient_info=patient,
    )
