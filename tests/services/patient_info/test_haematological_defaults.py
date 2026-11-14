"""CB #4392/#5378 default cutoffs, expressed in canonical lab units (#629)."""
import pytest
from django.test import override_settings

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.querysets.trial import TrialQuerySet
from trials.models import Trial
from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.trial_details.trial_attributes import TrialAttributes


DISEASES = (
    'multiple myeloma', 'follicular lymphoma', 'breast cancer',
    'chronic lymphocytic leukemia', 'mantle cell lymphoma',
)


@pytest.mark.parametrize('anc,platelets,hb,expected', [
    (1000, 75000, 9, True),
    (999, 75000, 9, False),
    (1000, 74999, 9, False),
    (1000, 75000, 8.99, False),
    (1001, 75001, 9.01, True),
    (1200, 80000, 10, True),
    (2000, 500, 12, False),
    (2000, 50000, 12, False),
    (0, 75000, 9, False),
    (1000, 0, 9, False),
    (1000, 75000, 0, False),
])
@pytest.mark.parametrize('scaled_units', [False, True])
def test_default_boundaries_and_unit_equivalence(anc, platelets, hb, expected, scaled_units):
    labs = {
        'absolute_neutrophile_count': anc,
        'absolute_neutrophile_count_units': 'CELLS/UL',
        'platelet_count': platelets,
        'platelet_count_units': 'CELLS/UL',
        'hemoglobin_level': hb,
        'hemoglobin_level_units': 'G/DL',
    }
    if scaled_units:
        labs.update(
            absolute_neutrophile_count=anc * 1_000_000,
            absolute_neutrophile_count_units='CELLS/L',
            platelet_count=platelets * 1_000_000,
            platelet_count_units='CELLS/L',
        )
    patient = _build_in_memory(labs)
    assert patient.haematological_adequacy_status is expected
    assert PatientInfoAttributes(patient).haematological_adequacy_status is expected


#: One row per lab: its inline field, the unit field, a value that fails every
#: default, an unusable authored minimum (below the sane band) and a usable one.
ZERO_LABS = (
    # lab, unit field, units, unusable min (below the band), usable min, valid max
    ('absolute_neutrophile_count', 'absolute_neutrophile_count_units', 'CELLS/UL', 5, 1000, 150_000),
    ('platelet_count', 'platelet_count_units', 'CELLS/UL', 5, 75000, 1_500_000),
    ('hemoglobin_level', 'hemoglobin_level_units', 'G/DL', 0.5, 9, 20),
)


@pytest.mark.django_db
@pytest.mark.parametrize('lab,units_field,units,unusable_min,usable_min,valid_max', ZERO_LABS)
def test_a_zero_lab_is_a_reading_not_missing_data(lab, units_field, units, unusable_min, usable_min, valid_max):
    """#5523: a zero lab must be judged by the trial's bounds, not skipped.

    With no usable minimum (the authored one sits outside the sane range) the
    trial admits the patient on its maximum; the old `is_attr_blank` gate turned
    the zero into "no reading" and rejected the trial outright.
    """
    from tests.factories import TrialFactory

    labs = {
        'absolute_neutrophile_count': 1000, 'absolute_neutrophile_count_units': 'CELLS/UL',
        'platelet_count': 75000, 'platelet_count_units': 'CELLS/UL',
        'hemoglobin_level': 9, 'hemoglobin_level_units': 'G/DL',
    }
    labs[lab] = 0
    labs[units_field] = units
    patient = _build_in_memory(labs)
    attributes = PatientInfoAttributes(patient)

    admitted = TrialFactory(
        haematological_adequacy_required=True,
        **{f'{lab}_min': unusable_min, f'{lab}_max': valid_max},
    )
    assert Trial.objects.filter(pk=admitted.pk)._eligible_for_haematological_adequacy(patient).exists()
    # The per-trial verdict and the matcher must agree with the pre-filter: a
    # trial that survives the SQL and then reports `not_matched` is the same
    # search/detail split this issue exists to remove.
    assert attributes.haematological_adequacy_status_for_trial(admitted) is True
    assert UserToTrialAttrMatcher(admitted, patient).attr_match_status('haematological_adequacy_status') == 'matched'

    rejected = TrialFactory(
        haematological_adequacy_required=True,
        **{f'{lab}_min': usable_min},
    )
    assert not Trial.objects.filter(pk=rejected.pk)._eligible_for_haematological_adequacy(patient).exists()
    assert attributes.haematological_adequacy_status_for_trial(rejected) is False
    assert UserToTrialAttrMatcher(rejected, patient).attr_match_status('haematological_adequacy_status') == 'not_matched'


@pytest.mark.parametrize('missing', ['absolute_neutrophile_count', 'platelet_count', 'hemoglobin_level'])
def test_inline_explicit_unknown_is_unchanged(missing):
    data = {
        'absolute_neutrophile_count': 1000, 'platelet_count': 75000,
        'hemoglobin_level': 9, 'haematological_adequacy_status': None,
    }
    data[missing] = None
    assert _build_in_memory(data).haematological_adequacy_status is None


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', [_build_in_memory, build_patient_info_from_promop_row])
@pytest.mark.parametrize('anc,platelets,expected', [(1200, 80000, True), (2000, 50000, False)])
def test_default_verdict_pool_and_details_for_both_sources(disease, builder, anc, platelets, expected):
    patient = builder({
        'disease': disease, 'absolute_neutrophile_count': anc,
        'absolute_neutrophile_count_units': 'CELLS/UL',
        'platelet_count': platelets, 'platelet_count_units': 'CELLS/UL',
        'hemoglobin_level': 12, 'hemoglobin_level_units': 'G/DL',
    })
    assert patient.haematological_adequacy_status is expected
    trial = Trial.objects.create(disease=disease, haematological_adequacy_required=True)
    verdict = UserToTrialAttrMatcher(trial, patient).attr_match_status('haematological_adequacy_status')
    assert verdict == ('matched' if expected else 'not_matched')
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists() is expected
    details = TrialAttributes(trial, patient).get_user_details(subform_attrs={})
    assert details['haematological_adequacy_required']['uvalue'] is expected
