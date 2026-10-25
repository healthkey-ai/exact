"""ANC/platelet sane-range guards on this baseline (#632).

CB guards the ANC and platelet min/max comparison with a canonical cells/µL
band, so a trial threshold still stored on the old scale is treated as "no
constraint" rather than compared on the wrong scale. The band and the guard
were closed as EXACT #380, but were absent from the audited baseline; these
tests pin the restoration and its interaction with the trial-aware adequacy
port from #630.

WBC is deliberately NOT guarded here: EXACT compares WBC on cells/L while CB's
band is cells/µL, so a host on CB's contract injects both through
EXACT_ATTRS_MAPPING_OVERRIDES instead.
"""
import pytest
from django.test import override_settings

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
from exact_matching.querysets.trial import TrialQuerySet
from trials.models import Trial
from trials.services.patient_info.resolve import _build_in_memory

MM = 'multiple myeloma'
ANC_MIN, ANC_MAX = 'absolute_neutrophile_count_min', 'absolute_neutrophile_count_max'
PLT_MIN, PLT_MAX = 'platelet_count_min', 'platelet_count_max'


def _patient(**fields):
    return _build_in_memory(dict(fields, disease=MM))


def _readings(**fields):
    base = dict(
        absolute_neutrophile_count=2000, absolute_neutrophile_count_units='CELLS/UL',
        platelet_count=100000, platelet_count_units='CELLS/UL',
        hemoglobin_level=12, hemoglobin_level_units='G/DL',
    )
    base.update(fields)
    return base


def _in_pool(trial, patient):
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    return pool.exists()


def test_config_restores_the_cb_bands():
    anc = USER_TO_TRIAL_ATTRS_MAPPING['absolute_neutrophile_count']
    platelets = USER_TO_TRIAL_ATTRS_MAPPING['platelet_count']
    assert anc['sane_range'] == (10, 200000)
    assert platelets['sane_range'] == (10000, 2000000)
    assert anc['default_unit'] == platelets['default_unit'] == 'cells/UL'


def test_wbc_stays_unguarded_on_the_cells_per_litre_contract():
    """Copying CB's per-µL band onto a per-L comparison would disable the guard."""
    wbc = USER_TO_TRIAL_ATTRS_MAPPING['white_blood_cell_count']
    assert wbc['default_unit'] == 'cells/L'
    assert 'sane_range' not in wbc


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('attr_min,attr_max,value,trial_fields', [
    # Each bound is out of band AND would exclude the patient if applied: the
    # only reason the trial survives is the guard.
    # ANC minimum at 1,000,000/µL (old thousands/µL scale) as a floor.
    (ANC_MIN, ANC_MAX, 2000, {ANC_MIN: 1_000_000}),
    # ANC maximum of 0 as a ceiling.
    (ANC_MIN, ANC_MAX, 2000, {ANC_MAX: 0}),
    # Platelet minimum at 400 (old thousands/µL scale) as a floor.
    (PLT_MIN, PLT_MAX, 100000, {PLT_MIN: 400}),
    # Platelet maximum at 400 as a ceiling.
    (PLT_MIN, PLT_MAX, 100000, {PLT_MAX: 400}),
])
def test_out_of_band_threshold_is_no_constraint(attr_min, attr_max, value, trial_fields):
    measured = 'absolute_neutrophile_count' if attr_min == ANC_MIN else 'platelet_count'
    patient = _patient(**_readings(**{measured: value}))
    trial = Trial.objects.create(disease=MM, **trial_fields)
    assert _in_pool(trial, patient)


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('attr_min,attr_max,value,trial_fields,expected', [
    # In-band bounds still decide.
    (ANC_MIN, ANC_MAX, 1200, {ANC_MIN: 1500}, False),
    (ANC_MIN, ANC_MAX, 2000, {ANC_MIN: 1500}, True),
    (ANC_MIN, ANC_MAX, 2000, {ANC_MAX: 1800}, False),
    (PLT_MIN, PLT_MAX, 100000, {PLT_MIN: 120000}, False),
    (PLT_MIN, PLT_MAX, 100000, {PLT_MAX: 90000}, False),
    # Exactly on a band edge is IN band, so the bound applies — in both
    # directions, which is what pins the inclusive comparison.
    (ANC_MIN, ANC_MAX, 2000, {ANC_MIN: 200000}, False),
    (ANC_MIN, ANC_MAX, 2000, {ANC_MAX: 10}, False),
    (ANC_MIN, ANC_MAX, 2000, {ANC_MIN: 10}, True),
    (PLT_MIN, PLT_MAX, 100000, {PLT_MIN: 10000}, True),
    (PLT_MIN, PLT_MAX, 100000, {PLT_MAX: 10000}, False),
    (PLT_MIN, PLT_MAX, 100000, {PLT_MIN: 2000000}, False),
])
def test_in_band_bounds_keep_deciding(attr_min, attr_max, value, trial_fields, expected):
    measured = 'absolute_neutrophile_count' if attr_min == ANC_MIN else 'platelet_count'
    patient = _patient(**_readings(**{measured: value}))
    trial = Trial.objects.create(disease=MM, **trial_fields)
    assert _in_pool(trial, patient) is expected


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('patient_anc,patient_plt,trial_fields,attr,expected', [
    (2000, 100000, {ANC_MIN: 1_000_000}, 'absolute_neutrophile_count', 'matched'),
    (1200, 100000, {ANC_MIN: 1500}, 'absolute_neutrophile_count', 'not_matched'),
    (2000, 100000, {ANC_MAX: 0}, 'absolute_neutrophile_count', 'matched'),
    (2000, 100000, {ANC_MAX: 1800}, 'absolute_neutrophile_count', 'not_matched'),
    (2000, 100000, {PLT_MIN: 400}, 'platelet_count', 'matched'),
    (2000, 80000, {PLT_MIN: 120000}, 'platelet_count', 'not_matched'),
    (2000, 100000, {PLT_MAX: 400}, 'platelet_count', 'matched'),
    (2000, 100000, {PLT_MAX: 90000}, 'platelet_count', 'not_matched'),
])
def test_matcher_and_pool_agree(patient_anc, patient_plt, trial_fields, attr, expected):
    patient = _patient(**_readings(
        absolute_neutrophile_count=patient_anc, platelet_count=patient_plt,
    ))
    trial = Trial.objects.create(disease=MM, **trial_fields)
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(attr) == expected
    assert _in_pool(trial, patient) is (expected != 'not_matched')


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_mixed_valid_and_invalid_bounds_use_only_the_valid_one():
    patient = _patient(**_readings(absolute_neutrophile_count=2000))
    # Valid minimum plus an old-scale MAXIMUM of 0, which would exclude the
    # patient as a ceiling if the guard did not ignore it.
    trial = Trial.objects.create(
        disease=MM, absolute_neutrophile_count_min=1500,
        absolute_neutrophile_count_max=0,
    )
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(
        'absolute_neutrophile_count') == 'matched'
    assert _in_pool(trial, patient)
    # Same pair, but the patient misses the valid minimum.
    low = _patient(**_readings(absolute_neutrophile_count=1200))
    assert not _in_pool(trial, low)


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('fields', [
    {'absolute_neutrophile_count_min': 1_000_000},
    {'absolute_neutrophile_count_max': 0},
    {'platelet_count_min': 400},
    {'platelet_count_max': 400},
])
def test_trial_aware_adequacy_ignores_an_old_scale_bound(fields):
    """#630's adequacy pass reads the same sane_range metadata."""
    patient = _patient(**_readings())
    trial = Trial.objects.create(disease=MM, haematological_adequacy_required=True, **fields)
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(
        'haematological_adequacy_status') == 'matched'
    assert _in_pool(trial, patient)


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_adequacy_still_rejects_a_genuinely_low_reading():
    """The guard must not swallow in-band limits or the defaults."""
    patient = _patient(**_readings(absolute_neutrophile_count=800))
    trial = Trial.objects.create(disease=MM, haematological_adequacy_required=True)
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(
        'haematological_adequacy_status') == 'not_matched'
    assert not _in_pool(trial, patient)


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('attr_min,trial_fields', [
    (ANC_MIN, {ANC_MIN: 1_000_000}),
    (PLT_MIN, {PLT_MIN: 400}),
])
def test_blank_patient_keeps_the_trial_potential(attr_min, trial_fields):
    """A blank reading is not a rejection, guarded bound or not."""
    measured = 'absolute_neutrophile_count' if attr_min == ANC_MIN else 'platelet_count'
    patient = _patient(**{**_readings(), measured: None})
    trial = Trial.objects.create(disease=MM, **trial_fields)
    assert _in_pool(trial, patient)
