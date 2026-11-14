"""#4832 first reconcile: blank computed bool_restriction (meets_crab) must be
`unknown`/potential, not a hard `not_matched`.

A trial that requires meets_crab=True, matched against a patient whose CRAB
status is unresolved (blank), diverges today: the matcher hard-fails it
(`not_matched` -> not_eligible) while the SQL path keeps it as `potential`.
The clinically-correct verdict is `potential` (fill in the CRAB data), so the
matcher is the side to fix.
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.matching import status_equivalence as se
from tests.factories import TrialFactory


@pytest.mark.django_db
def test_blank_meets_crab_precondition():
    """The fix's precondition: for a bare MM patient, meets_crab is unresolved
    (blank / None), which is the input that used to be coerced to False."""
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)
    attrs = PatientInfoAttributes(pi)
    assert attrs.get_value('meets_crab') is None
    assert attrs.is_attr_blank('meets_crab') is True


@pytest.mark.django_db
def test_blank_meets_crab_is_potential_not_rejected():
    """The reconcile: blank meets_crab vs a trial requiring it -> potential."""
    trial = TrialFactory(disease='multiple myeloma', meets_crab=True)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)

    verdict = UserToTrialAttrMatcher(trial, pi).trial_match_status()
    assert verdict == 'potential', (
        f"blank meets_crab against a meets_crab=True trial should be potential, "
        f"got {verdict!r}"
    )


@pytest.mark.django_db
def test_no_sql_matcher_divergence_on_blank_meets_crab():
    """The comparator (the #4832 contract) must see no divergence for this case."""
    trial = TrialFactory(disease='multiple myeloma', meets_crab=True)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)
    base = Trial.objects.filter(id=trial.id)

    divs = se.compare(base, pi)
    assert divs == [], (
        f"blank meets_crab still diverges: "
        f"{[(d.direction, d.matcher_not_matched_attrs) for d in divs]}"
    )


@pytest.mark.django_db
def test_blank_meets_crab_matched_when_trial_does_not_require():
    """Guard against over-firing: a blank bool against a trial that does NOT
    require it stays eligible (the value == trial short-circuit runs first)."""
    trial = TrialFactory(disease='multiple myeloma', meets_crab=False)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)

    verdict = UserToTrialAttrMatcher(trial, pi).trial_match_status()
    assert verdict == 'eligible', (
        f"blank meets_crab vs a non-requiring trial should be eligible, got {verdict!r}"
    )


@pytest.mark.django_db
def test_peer_bool_no_hiv_blank_is_potential_isnull_branch():
    """Peer coverage: the fix applies to every bool_restriction, not just the
    IS-NOT-TRUE class. `no_hiv_status` routes through the SQL `IS NULL` branch;
    a blank value against a trial that requires it must reconcile to potential
    (matcher) with no divergence."""
    trial = TrialFactory(disease='multiple myeloma', no_hiv_required=True)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65, no_hiv_status=None)
    base = Trial.objects.filter(id=trial.id)

    verdict = UserToTrialAttrMatcher(trial, pi).trial_match_status()
    assert verdict == 'potential', f"blank no_hiv_status vs a requiring trial, got {verdict!r}"
    assert se.compare(base, pi) == []


@pytest.mark.django_db
def test_definite_false_meets_crab_still_rejected():
    """Guard: an established negative (meets_crab computed False, not blank) must
    still be not_eligible against a meets_crab=True trial — the fix only rescues
    the BLANK case, not a real negative."""
    trial = TrialFactory(disease='multiple myeloma', meets_crab=True)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65, meets_crab=False)

    verdict = UserToTrialAttrMatcher(trial, pi).trial_match_status()
    assert verdict == 'not_eligible', (
        f"a definite meets_crab=False should stay not_eligible, got {verdict!r}"
    )
