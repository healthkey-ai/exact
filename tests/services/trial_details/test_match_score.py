"""The score the list card and the detail header share."""
import pytest

from trials.services.trial_details.match_score import score_from_criteria

pytestmark = pytest.mark.django_db


def _criteria(*statuses):
    return [{"name": f"c{i}", "matchingType": status}
            for i, status in enumerate(statuses)]


def test_score_is_matched_over_the_rendered_criteria():
    # 4 of 5: the trial's five restrictions, one still unknown.
    assert score_from_criteria(_criteria(
        "unknown", "matched", "matched", "matched", "matched",
    )) == 80


def test_any_not_matched_is_zero():
    assert score_from_criteria(_criteria(
        "matched", "not_matched", "unknown",
    )) == 0


def test_no_criteria_is_zero():
    assert score_from_criteria([]) == 0
    assert score_from_criteria(None) == 0


def test_rank_cache_fingerprints_the_patient_facts():
    """The ranking cache key must change when the patient's facts change.

    A caller may send only `person_id`; the inline payload is then absent and
    two patients with the same candidate set would share a cached order.
    """
    from trials.api.trials_views import TrialsViewSet
    from trials.services.patient_info.patient_info import PatientInfo

    a = PatientInfo(disease='multiple myeloma', patient_age=60)
    b = PatientInfo(disease='multiple myeloma', patient_age=61)
    assert TrialsViewSet._patient_facts_fingerprint(a) != TrialsViewSet._patient_facts_fingerprint(b)
    assert TrialsViewSet._patient_facts_fingerprint(a) == TrialsViewSet._patient_facts_fingerprint(
        PatientInfo(disease='multiple myeloma', patient_age=60)
    )
