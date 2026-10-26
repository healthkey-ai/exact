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


@pytest.mark.django_db
def test_a_zero_lab_is_a_reading_not_missing_data():
    """#5523: a zero ANC must be judged by the trial's bounds, not skipped.

    With no usable minimum (the authored one sits outside the sane range) the
    trial admits the patient on its maximum; the old `is_attr_blank` gate turned
    the zero into "no reading" and rejected the trial outright.
    """
    from tests.factories import TrialFactory

    patient = _build_in_memory({
        'absolute_neutrophile_count': 0, 'absolute_neutrophile_count_units': 'CELLS/UL',
        'platelet_count': 75000, 'platelet_count_units': 'CELLS/UL',
        'hemoglobin_level': 9, 'hemoglobin_level_units': 'G/DL',
    })

    unusable_min = TrialFactory(
        haematological_adequacy_required=True,
        absolute_neutrophile_count_min=5,
        absolute_neutrophile_count_max=10000,
    )
    assert Trial.objects.filter(pk=unusable_min.pk)._eligible_for_haematological_adequacy(patient).exists()
    # The per-trial verdict and the matcher must agree with the pre-filter: a
    # trial that survives the SQL and then reports `not_matched` is the same
    # search/detail split this issue exists to remove.
    assert PatientInfoAttributes(patient).haematological_adequacy_status_for_trial(unusable_min) is True
    assert UserToTrialAttrMatcher(unusable_min, patient).attr_match_status('haematological_adequacy_status') == 'matched'

    usable_min = TrialFactory(
        haematological_adequacy_required=True,
        absolute_neutrophile_count_min=1000,
    )
    assert not Trial.objects.filter(pk=usable_min.pk)._eligible_for_haematological_adequacy(patient).exists()


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
