"""
TherapyMatchProfile seam (OMOP cutover, port of CB epic #4447).

The matcher + queryset read trial therapy column names from this single profile.
These tests pin the active (legacy) names so flipping to the OMOP columns is an
intentional, reviewed change — not an accident.
"""
import pytest

from trials.services.therapy_match_profile import (
    THERAPY_MATCH_PROFILE,
    TherapyMatchProfile,
)

# Pure-logic tests, but the session-scoped DB-seed fixture (tests/conftest.py)
# only initializes correctly when the session has a django_db test; mark so the
# file is runnable standalone.
pytestmark = pytest.mark.django_db


def test_active_profile_targets_legacy_columns():
    assert THERAPY_MATCH_PROFILE.therapies_required == 'therapies_required'
    assert THERAPY_MATCH_PROFILE.therapies_excluded == 'therapies_excluded'
    assert THERAPY_MATCH_PROFILE.therapy_components_required == 'therapy_components_required'
    assert THERAPY_MATCH_PROFILE.therapy_components_excluded == 'therapy_components_excluded'
    assert THERAPY_MATCH_PROFILE.therapy_types_required == 'therapy_types_required'
    assert THERAPY_MATCH_PROFILE.therapy_types_excluded == 'therapy_types_excluded'
    assert THERAPY_MATCH_PROFILE.planned_therapies_required == 'planned_therapies_required'
    assert THERAPY_MATCH_PROFILE.planned_therapies_excluded == 'planned_therapies_excluded'
    assert THERAPY_MATCH_PROFILE.supportive_therapies_required == 'supportive_therapies_required'
    assert THERAPY_MATCH_PROFILE.supportive_therapies_excluded == 'supportive_therapies_excluded'


def test_profile_is_frozen():
    # Swapping the seam must be a deliberate module-level override, not a mutation.
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        THERAPY_MATCH_PROFILE.therapies_required = 'omop_therapies_required'


def test_an_omop_profile_can_be_constructed():
    # The cutover overrides the profile with omop_* column names in one place.
    omop = TherapyMatchProfile(
        therapies_required='omop_therapies_required',
        therapies_excluded='omop_therapies_excluded',
        therapy_components_required='omop_therapy_components_required',
        therapy_components_excluded='omop_therapy_components_excluded',
        therapy_types_required='omop_therapy_types_required',
        therapy_types_excluded='omop_therapy_types_excluded',
        planned_therapies_required='omop_planned_therapies_required',
        planned_therapies_excluded='omop_planned_therapies_excluded',
        supportive_therapies_required='omop_supportive_therapies_required',
        supportive_therapies_excluded='omop_supportive_therapies_excluded',
    )
    assert omop.therapies_required == 'omop_therapies_required'
