"""Bulky-disease criteria keep CB's three-valued semantics (#626).

The entry needs CB's `criteria_*` metadata. Without it EXACT falls through to
the generic computed-overlap branch, which cannot distinguish a confirmed
negative (every source size answered, none reaching a threshold) from a
criterion that was never answered, and cannot see that a required criterion is
undetermined because ITS source field is blank.

Expected verdicts below are CB's `_match_criteria_count` rule for an entry with
only `criteria_required_attr` and no min_count/sufficient/excluded attrs:
any required code present -> matched; otherwise any required code whose sources
are blank -> unknown; otherwise not_matched.

SQL counterpart: `potential_attrs_to_check` now builds the per-criterion
expression, which answers NULL for a trial the patient satisfies and 1 for a
trial that stays potential. Collapsing `not_matched` to potential there is the
documented coarse-filter behaviour shared with high-risk MCL (#4416/#186), not
something this port changes.
"""
import pytest
from django.db.models.expressions import RawSQL
from django.test import override_settings

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
from exact_matching.patient_info.patient_info_attributes import (
    BULKY_DISEASE_CRITERIA_SOURCES,
    PatientInfoAttributes,
)
from exact_matching.user_to_trial_attrs_mapper import UserToTrialAttrsMapper
from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.resolve import _build_in_memory

MCL = 'mantle cell lymphoma'
ATTR = 'bulky_disease_criteria'


def _patient(**kwargs):
    # normalize_patient_info fills the derived `bulky_disease_criteria` field
    # that `criteria_derived` reads, so build through the same DTO boundary the
    # two resolution paths use.
    return _build_in_memory(dict(kwargs, disease=MCL))


def _verdict(trial, patient):
    return UserToTrialAttrMatcher(trial, patient).attr_match_status(ATTR)


def _sql_potential(trial, patient):
    """1 when the trial stays potential, NULL when the patient satisfies it."""
    expression = UserToTrialAttrsMapper().potential_attrs_to_check(patient_info=patient)[ATTR]
    return Trial.objects.filter(pk=trial.pk).annotate(
        potential=RawSQL(expression, []),
    ).values_list('potential', flat=True).get()


def test_metadata_is_portable_from_cb():
    meta = USER_TO_TRIAL_ATTRS_MAPPING[ATTR]
    assert meta['criteria_count_match'] is True
    assert meta['criteria_required_attr'] == 'bulky_disease_criteria_required'
    assert meta['criteria_derived'](PatientInfo()) is None
    assert meta['criteria_unknown_codes'](PatientInfoAttributes(PatientInfo()), ['bulky_lesion_5cm'])
    assert meta['criteria_all_unknown_codes'](PatientInfoAttributes(PatientInfo())) == set(
        BULKY_DISEASE_CRITERIA_SOURCES,
    )


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_fully_answered_subthreshold_sizes_are_a_confirmed_negative():
    """Every source answered, nothing qualifies -> not_matched, not unknown."""
    patient = _patient(largest_lesion_size=4, largest_lymph_node_size=4, spleen_size=12)
    trial = Trial.objects.create(disease=MCL, bulky_disease_criteria_required=['bulky_lesion_5cm'])
    assert PatientInfoAttributes(patient).bulky_disease_criteria is None
    assert _verdict(trial, patient) == 'not_matched'


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_unanswered_source_keeps_the_trial_potential():
    """The required criterion's own source is blank -> unknown."""
    patient = _patient(largest_lesion_size=4, spleen_size=12)
    trial = Trial.objects.create(disease=MCL, bulky_disease_criteria_required=['bulky_node_5cm'])
    assert PatientInfoAttributes(patient).bulky_disease_criteria is None
    assert _verdict(trial, patient) == 'unknown'
    assert _sql_potential(trial, patient) == 1


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_one_satisfied_criterion_matches_despite_another_blank_source():
    """A met criterion settles inclusion even when a sibling is unanswered."""
    patient = _patient(largest_lesion_size=6, spleen_size=12)
    trial = Trial.objects.create(
        disease=MCL,
        bulky_disease_criteria_required=['bulky_lesion_5cm', 'bulky_node_5cm'],
    )
    assert PatientInfoAttributes(patient).bulky_disease_criteria == 'bulky_lesion_5cm'
    assert _verdict(trial, patient) == 'matched'
    assert _sql_potential(trial, patient) is None


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_fully_answered_without_the_required_threshold_is_a_negative():
    patient = _patient(largest_lesion_size=6, largest_lymph_node_size=3, spleen_size=12)
    trial = Trial.objects.create(disease=MCL, bulky_disease_criteria_required=['bulky_node_5cm'])
    assert _verdict(trial, patient) == 'not_matched'


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_criterion_below_threshold_with_its_source_answered_is_a_negative():
    patient = _patient(largest_lesion_size=4)
    trial = Trial.objects.create(disease=MCL, bulky_disease_criteria_required=['bulky_lesion_5cm'])
    assert _verdict(trial, patient) == 'not_matched'


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_spleen_gt_and_gte_share_one_source():
    """Both published spleen cut-offs derive from `spleen_size`."""
    patient = _patient(spleen_size=21)
    derived = PatientInfoAttributes(patient).bulky_disease_criteria
    assert 'bulky_spleen_20cm_gt' in derived and 'bulky_spleen_20cm_gte' in derived
    trial = Trial.objects.create(disease=MCL, bulky_disease_criteria_required=['bulky_spleen_20cm_gt'])
    assert _verdict(trial, patient) == 'matched'
    assert _sql_potential(trial, patient) is None


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_no_requirement_matches():
    patient = _patient(largest_lesion_size=4)
    trial = Trial.objects.create(disease=MCL)
    assert _verdict(trial, patient) == 'matched'
    assert _sql_potential(trial, patient) is None


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_all_three_size_sources_answered_but_none_reaching_a_threshold():
    """Guard the "all unknown" helper: answered sources must not read unknown."""
    patient = _patient(largest_lesion_size=1, largest_lymph_node_size=1, spleen_size=1)
    attrs = PatientInfoAttributes(patient)
    assert attrs.bulky_disease_criteria_all_unknown_codes() == set()
    trial = Trial.objects.create(disease=MCL, bulky_disease_criteria_required=['bulky_lesion_5cm'])
    assert _verdict(trial, patient) == 'not_matched'
