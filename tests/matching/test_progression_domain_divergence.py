"""#5026: an out-of-domain `progression` value must read as unanswered, not as a
hard rejection.

`PatientInfo.progression` has a closed domain (`''` / `'active'` / `'smoldering'`)
that nothing validates on the way in, so real records carry values like `'0'` or
`'Getting worse'`. The SQL filter recognises only the domain members and no-ops on
anything else (trial kept, and a non-empty value was counted as answered ->
Eligible), while the matcher's final `else` branch returned `not_matched` ->
Not Eligible. The census in cancerbot#4841 measured 2735 diverging (patient,
trial) pairs from four patients, one losing 43% of its corpus.

Contract: an unrecognised value is not an answer -> `unknown` / potential on both
paths, exactly like a blank.
"""
import pytest

from trials.models import Trial
from trials.services.matching import status_equivalence as se
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

OUT_OF_DOMAIN = ['0', 'Getting worse', 'slow', 'high risk', 'Active', ' active']


def _mm_patient(progression):
    return PatientInfo(disease='multiple myeloma', patient_age=65, progression=progression)


@pytest.mark.parametrize('value', OUT_OF_DOMAIN)
def test_out_of_domain_progression_reads_as_unanswered(value):
    """The precondition: the shared `is_attr_blank` primitive treats it as blank.

    Membership is exact — 'Active' and ' active' are not the stored code, and
    accepting them here would only move the disagreement to the handler, which
    compares the raw value.
    """
    assert PatientInfoAttributes(_mm_patient(value)).is_attr_blank('progression') is True


@pytest.mark.parametrize('value', ['active', 'smoldering'])
def test_domain_values_still_count_as_answered(value):
    assert PatientInfoAttributes(_mm_patient(value)).is_attr_blank('progression') is False


@pytest.mark.django_db
@pytest.mark.parametrize('value', OUT_OF_DOMAIN)
def test_out_of_domain_progression_is_potential_not_rejected(value):
    trial = TrialFactory(disease='multiple myeloma', disease_progression_active_required=True)

    verdict = UserToTrialAttrMatcher(trial, _mm_patient(value)).trial_match_status()

    assert verdict == 'potential', (
        f"progression={value!r} against an active-required trial should be potential, "
        f"got {verdict!r}"
    )


@pytest.mark.django_db
@pytest.mark.parametrize('value', OUT_OF_DOMAIN + ['', None])
def test_no_sql_matcher_divergence_on_out_of_domain_progression(value):
    """The #4832 contract: the two paths must agree, whatever the value."""
    trial = TrialFactory(disease='multiple myeloma', disease_progression_active_required=True)
    base = Trial.objects.filter(id=trial.id)

    divs = se.compare(base, _mm_patient(value))

    assert divs == [], (
        f"progression={value!r} still diverges: "
        f"{[(d.direction, d.matcher_unknown_attrs, d.matcher_not_matched_attrs) for d in divs]}"
    )


@pytest.mark.django_db
def test_conflicting_domain_value_still_rejects():
    """Guard against over-firing: a VALID value that contradicts the trial is
    still a definite no. Only unrecognised values became unknown."""
    trial = TrialFactory(disease='multiple myeloma', disease_progression_active_required=True)

    verdict = UserToTrialAttrMatcher(trial, _mm_patient('smoldering')).trial_match_status()

    assert verdict == 'not_eligible'


@pytest.mark.django_db
def test_matching_domain_value_still_eligible():
    trial = TrialFactory(disease='multiple myeloma', disease_progression_active_required=True)

    verdict = UserToTrialAttrMatcher(trial, _mm_patient('active')).trial_match_status()

    assert verdict == 'eligible'


@pytest.mark.django_db
def test_trial_without_a_progression_requirement_is_unaffected():
    """A trial that requires neither flag short-circuits to matched, so an
    out-of-domain value must not turn it into a potential."""
    trial = TrialFactory(disease='multiple myeloma')

    verdict = UserToTrialAttrMatcher(trial, _mm_patient('Getting worse')).trial_match_status()

    assert verdict == 'eligible'
