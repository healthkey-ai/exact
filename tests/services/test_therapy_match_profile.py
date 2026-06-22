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


def test_active_profile_defaults_to_legacy_columns():
    # EXACT_OMOP_THERAPY defaults off -> legacy internal-code columns.
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
    # the three mapped levels flip to the omop_* columns...
    assert THERAPY_MATCH_PROFILE.therapies_required == 'omop_therapies_required'
    assert THERAPY_MATCH_PROFILE.therapies_excluded == 'omop_therapies_excluded'
    assert THERAPY_MATCH_PROFILE.therapy_components_required == 'omop_therapy_components_required'
    assert THERAPY_MATCH_PROFILE.therapy_components_excluded == 'omop_therapy_components_excluded'
    assert THERAPY_MATCH_PROFILE.therapy_types_required == 'omop_therapy_types_required'
    assert THERAPY_MATCH_PROFILE.therapy_types_excluded == 'omop_therapy_types_excluded'
    # ...but planned/supportive stay legacy (their vocabs have no concept_ids yet).
    assert THERAPY_MATCH_PROFILE.planned_therapies_required == 'planned_therapies_required'
    assert THERAPY_MATCH_PROFILE.planned_therapies_excluded == 'planned_therapies_excluded'
    assert THERAPY_MATCH_PROFILE.supportive_therapies_required == 'supportive_therapies_required'
    assert THERAPY_MATCH_PROFILE.supportive_therapies_excluded == 'supportive_therapies_excluded'


def test_get_therapy_match_profile_switches_on_flag():
    assert get_therapy_match_profile() is LEGACY_THERAPY_MATCH_PROFILE
    with override_settings(EXACT_OMOP_THERAPY=True):
        assert get_therapy_match_profile() is OMOP_THERAPY_MATCH_PROFILE


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
    # the shipped OMOP profile flips exactly the three mapped levels
    assert OMOP_THERAPY_MATCH_PROFILE.therapies_required == 'omop_therapies_required'
    assert OMOP_THERAPY_MATCH_PROFILE.planned_therapies_required == 'planned_therapies_required'
