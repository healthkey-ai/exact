"""The PRomop aggregate must survive adaptation into EXACT matching."""
import pytest
from rest_framework.exceptions import ValidationError

from tests.factories import TrialFactory
from trials.models import Trial
from trials.services.patient_info.ctomop_adapter import build_patient_info_from_ctomop_row
from trials.services.patient_info.normalize import normalize_patient_info
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('key', ['tp53_disruption', 'tp53Disruption'])
@pytest.mark.parametrize('value', [None, True, False])
def test_inline_aggregate_survives_repeated_normalization(key, value):
    pi = _build_in_memory({'disease': 'chronic lymphocytic leukemia', key: value})
    normalize_patient_info(pi)
    assert pi.tp53_disruption is value
    assert PatientInfoAttributes(pi).tp53_disruption is value


@pytest.mark.parametrize('value', [None, True, False])
def test_source_row_preserves_explicit_aggregate(value):
    pi = build_patient_info_from_ctomop_row({'disease': 'chronic lymphocytic leukemia', 'tp53_disruption': value})
    assert pi.tp53_disruption is value
    assert PatientInfoAttributes(pi).tp53_disruption is value


@pytest.mark.parametrize('value', ['false', 0, 1, {}, []])
def test_non_boolean_aggregate_is_rejected(value):
    with pytest.raises(ValidationError):
        _build_in_memory({'tp53_disruption': value})


def test_omitted_aggregate_keeps_existing_legacy_marker_derivation():
    assert _build_in_memory({'molecular_markers': 'tp53Mutation'}).tp53_disruption is True
    assert _build_in_memory({'molecular_markers': ''}).tp53_disruption is False


@pytest.mark.parametrize('restriction', ['tp53_disruption_required', 'tp53_disruption_excluded'])
def test_unknown_is_not_a_confirmed_eligibility_match(restriction):
    pi = _build_in_memory({'disease': 'chronic lymphocytic leukemia', 'tp53_disruption': None})
    trial = TrialFactory(disease='chronic lymphocytic leukemia', **{restriction: True})
    assert UserToTrialAttrMatcher(trial, pi).attr_match_status('tp53_disruption') == 'unknown'
    # Unknown stays a candidate, with the criterion explicitly unresolved.
    assert Trial.objects.eligible_for_tp53_disruption(pi.tp53_disruption).filter(pk=trial.pk).exists()


@pytest.mark.parametrize('restriction,expected', [('tp53_disruption_required', 'matched'), ('tp53_disruption_excluded', 'not_matched')])
def test_explicit_positive_reaches_eligibility_without_legacy_markers(restriction, expected):
    pi = _build_in_memory({'disease': 'chronic lymphocytic leukemia', 'tp53_disruption': True})
    trial = TrialFactory(disease='chronic lymphocytic leukemia', **{restriction: True})
    assert UserToTrialAttrMatcher(trial, pi).attr_match_status('tp53_disruption') == expected
