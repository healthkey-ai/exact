"""Verbatim CB #5620 clinical-override seam, preserving EXACT adaptations."""
import pytest

from exact_matching.matcher import UserToTrialAttrMatcher
from trials.services.patient_info.patient_info import PatientInfo
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


def test_clinical_override_precedes_legacy_reads_and_is_copied(monkeypatch):
    verdicts = {'patient_age': 'not_matched'}
    matcher = UserToTrialAttrMatcher(TrialFactory(), PatientInfo(), attribute_verdicts=verdicts)
    verdicts['patient_age'] = 'matched'
    monkeypatch.setattr(matcher.patient_info_attr, 'get_value', lambda name: pytest.fail('legacy read'))
    assert matcher.attr_match_status('patient_age') == 'not_matched'


@pytest.mark.parametrize('status', ['unavailable', 'unsupported', None, True, 'eligible'])
def test_system_or_invalid_status_cannot_enter_clinical_seam(status):
    with pytest.raises(ValueError, match='attribute_verdict_status'):
        UserToTrialAttrMatcher(TrialFactory(), PatientInfo(), attribute_verdicts={'patient_age': status})


def test_unknown_attribute_cannot_enter_clinical_seam():
    with pytest.raises(ValueError, match='attribute_verdict_keys'):
        UserToTrialAttrMatcher(TrialFactory(), PatientInfo(), attribute_verdicts={'not_an_attribute': 'matched'})
