"""
TherapyMatchProfile seam (OMOP cutover, port of CB epic #4447).

The matcher + queryset read trial therapy column names from this single profile,
which flips between the legacy internal-code columns and the OMOP concept_id
columns based on the EXACT_OMOP_THERAPY setting. These tests pin both profiles and
the flag-driven switch so a cutover is an intentional, reviewed change.
"""
import dataclasses

import pytest
from django.test import override_settings

from trials.services.therapy_match_profile import (
    THERAPY_MATCH_PROFILE,
    LEGACY_THERAPY_MATCH_PROFILE,
    OMOP_THERAPY_MATCH_PROFILE,
    TherapyMatchProfile,
    get_therapy_match_profile,
    omop_therapy_enabled,
)

# Pure-logic tests, but the session-scoped DB-seed fixture (tests/conftest.py)
# only initializes correctly when the session has a django_db test; mark so the
# file is runnable standalone.
pytestmark = pytest.mark.django_db


@override_settings(EXACT_OMOP_THERAPY=True)
def test_active_profile_uses_omop_columns_when_flag_on():
    assert omop_therapy_enabled() is True
    assert THERAPY_MATCH_PROFILE.therapies_required == 'omop_therapies_required'
    assert THERAPY_MATCH_PROFILE.therapies_excluded == 'omop_therapies_excluded'
    assert THERAPY_MATCH_PROFILE.therapy_components_required == 'omop_therapy_components_required'
    assert THERAPY_MATCH_PROFILE.therapy_components_excluded == 'omop_therapy_components_excluded'
    # supportive flips too (#228, dedicated path wired by #4449)
    assert THERAPY_MATCH_PROFILE.supportive_therapies_required == 'omop_supportive_therapies_required'
    assert THERAPY_MATCH_PROFILE.supportive_therapies_excluded == 'omop_supportive_therapies_excluded'
    # types stay legacy even in OMOP mode (matched via CB category graph)
    assert THERAPY_MATCH_PROFILE.therapy_types_required == 'therapy_types_required'
    assert THERAPY_MATCH_PROFILE.therapy_types_excluded == 'therapy_types_excluded'
    # planned stays legacy (76/85 codes unmappable — #230)
    assert THERAPY_MATCH_PROFILE.planned_therapies_required == 'planned_therapies_required'


@override_settings(EXACT_OMOP_THERAPY=False)
def test_active_profile_uses_legacy_columns_when_flag_off():
    assert omop_therapy_enabled() is False
    assert THERAPY_MATCH_PROFILE.therapies_required == 'therapies_required'
    assert THERAPY_MATCH_PROFILE.therapies_excluded == 'therapies_excluded'
    assert THERAPY_MATCH_PROFILE.therapy_components_required == 'therapy_components_required'
    assert THERAPY_MATCH_PROFILE.therapy_components_excluded == 'therapy_components_excluded'
    assert THERAPY_MATCH_PROFILE.therapy_types_required == 'therapy_types_required'
    assert THERAPY_MATCH_PROFILE.therapy_types_excluded == 'therapy_types_excluded'
    assert THERAPY_MATCH_PROFILE.planned_therapies_required == 'planned_therapies_required'
    assert THERAPY_MATCH_PROFILE.supportive_therapies_required == 'supportive_therapies_required'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_active_profile_flips_mapped_levels_when_flag_on():
    assert omop_therapy_enabled() is True
    # regimen + component + supportive flip to the omop_* concept_id columns...
    assert THERAPY_MATCH_PROFILE.therapies_required == 'omop_therapies_required'
    assert THERAPY_MATCH_PROFILE.therapies_excluded == 'omop_therapies_excluded'
    assert THERAPY_MATCH_PROFILE.therapy_components_required == 'omop_therapy_components_required'
    assert THERAPY_MATCH_PROFILE.therapy_components_excluded == 'omop_therapy_components_excluded'
    assert THERAPY_MATCH_PROFILE.supportive_therapies_required == 'omop_supportive_therapies_required'
    # ...but types stay LEGACY (not OMOP-mapped — matched via the CB category graph),
    # as does planned (76/85 codes unmappable — #230).
    assert THERAPY_MATCH_PROFILE.therapy_types_required == 'therapy_types_required'
    assert THERAPY_MATCH_PROFILE.therapy_types_excluded == 'therapy_types_excluded'
    assert THERAPY_MATCH_PROFILE.planned_therapies_required == 'planned_therapies_required'


def test_get_therapy_match_profile_switches_on_flag():
    with override_settings(EXACT_OMOP_THERAPY=True):
        assert get_therapy_match_profile() is OMOP_THERAPY_MATCH_PROFILE
    with override_settings(EXACT_OMOP_THERAPY=False):
        assert get_therapy_match_profile() is LEGACY_THERAPY_MATCH_PROFILE


def test_underlying_profiles_are_frozen():
    # Swapping the seam must be a settings change, not a dataclass mutation.
    with pytest.raises(dataclasses.FrozenInstanceError):
        LEGACY_THERAPY_MATCH_PROFILE.therapies_required = 'omop_therapies_required'


def test_active_profile_view_is_read_only():
    # The settings-aware view refuses writes — flip via EXACT_OMOP_THERAPY instead.
    with pytest.raises(AttributeError):
        THERAPY_MATCH_PROFILE.therapies_required = 'omop_therapies_required'


def test_an_omop_profile_can_be_constructed():
    omop = TherapyMatchProfile(
        therapies_required='omop_therapies_required',
        therapies_excluded='omop_therapies_excluded',
    )
    assert omop.therapies_required == 'omop_therapies_required'
    # the shipped OMOP profile flips regimen + component + supportive; planned stays legacy
    assert OMOP_THERAPY_MATCH_PROFILE.therapies_required == 'omop_therapies_required'
    assert OMOP_THERAPY_MATCH_PROFILE.supportive_therapies_required == 'omop_supportive_therapies_required'
    assert OMOP_THERAPY_MATCH_PROFILE.planned_therapies_required == 'planned_therapies_required'


@pytest.mark.django_db
def test_supportive_matching_reads_omop_column_under_flag():
    """#228: with supportive wired (#4449), the LIVE matcher + queryset enforce the
    supportive pair via omop_supportive_* under the flag and legacy supportive_* off."""
    from trials.models import Trial
    from trials.services.patient_info.patient_info import PatientInfo
    from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
    from tests.factories import TrialFactory

    # Trial excludes a supportive drug at BOTH vocabularies: legacy CB code + OMOP cid.
    t = TrialFactory(
        supportive_therapies_excluded=['zz_sup_code'],
        omop_supportive_therapies_excluded=['555'],
    )

    with override_settings(EXACT_OMOP_THERAPY=True):
        # matcher: patient carrying the concept_id is excluded (reads omop column)
        pi = PatientInfo(disease='multiple myeloma', supportive_therapies=[{'therapy': '555'}])
        assert UserToTrialAttrMatcher(t, pi).attr_match_status('supportive_therapies') == 'not_matched'
        # the legacy code no longer excludes under the flag
        pi_legacy = PatientInfo(disease='multiple myeloma', supportive_therapies=[{'therapy': 'zz_sup_code'}])
        assert UserToTrialAttrMatcher(t, pi_legacy).attr_match_status('supportive_therapies') == 'matched'
        # queryset reads the omop column too
        assert not Trial.objects.filter(pk=t.pk).eligible_for_supportive_therapies(['555']).exists()

    with override_settings(EXACT_OMOP_THERAPY=False):
        # legacy: the CB code excludes; the concept_id does not
        pi_legacy = PatientInfo(disease='multiple myeloma', supportive_therapies=[{'therapy': 'zz_sup_code'}])
        assert UserToTrialAttrMatcher(t, pi_legacy).attr_match_status('supportive_therapies') == 'not_matched'
        assert not Trial.objects.filter(pk=t.pk).eligible_for_supportive_therapies(['zz_sup_code']).exists()
