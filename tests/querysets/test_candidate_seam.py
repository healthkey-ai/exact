"""CB #5620 candidate seam: do not prefilter typed criteria by legacy answers."""
import pytest

from tests.factories import TrialFactory
from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.study_preferences import StudyPreferences

pytestmark = pytest.mark.django_db


def test_candidates_keep_clinical_conflict_and_existing_visibility():
    trial = TrialFactory(age_low_limit=50)
    invisible = TrialFactory(age_low_limit=50)
    pi = PatientInfo(disease='multiple myeloma', patient_age=20)
    scope = Trial.objects.exclude(pk=invisible.pk)
    candidates, _ = scope.candidate_trials({}, StudyPreferences(), pi)
    filtered, _ = scope.filtered_trials({}, StudyPreferences(), pi)
    assert list(candidates.values_list('pk', flat=True)) == [trial.pk]
    assert not filtered.exists()
