"""
Tests for the ?explain=true query parameter.

The flag is threaded through two layers:
  1. TrialsViewSet.get_serializer_context() sets context['explain'] = True/False
  2. TrialSerializer.to_representation() reads it and populates matchReasons
     (non-null list) or leaves it null.
"""
import pytest
from unittest.mock import patch, MagicMock

from trials.api.trials_views import TrialsViewSet
from trials.api.trials_serializers import TrialSerializer
from tests.factories import TrialFactory
from trials.services.patient_info.patient_info import PatientInfo


# ---------------------------------------------------------------------------
# View layer: get_serializer_context sets the flag correctly
# ---------------------------------------------------------------------------

def _make_view(query_params: dict) -> TrialsViewSet:
    view = TrialsViewSet()
    view.action = 'list'
    view.format_kwarg = None
    mock_request = MagicMock()
    mock_request.query_params = query_params
    mock_request.data = {}
    mock_request.method = 'GET'
    view.request = mock_request
    return view


class TestExplainContext:

    def _get_context(self, query_params):
        view = _make_view(query_params)
        with patch.object(view, '_resolve_patient_info', return_value=None), \
             patch.object(view, '_resolve_study_preferences') as mock_prefs:
            mock_prefs.return_value = MagicMock(distance_units='km', recruitment_status=None)
            with patch('trials.api.trials_views.TrialsViewSet.get_serializer_context',
                       wraps=view.get_serializer_context):
                return view.get_serializer_context()

    def test_explain_true_when_param_is_true(self):
        ctx = self._get_context({'explain': 'true'})
        assert ctx['explain'] is True

    def test_explain_true_case_insensitive(self):
        ctx = self._get_context({'explain': 'True'})
        assert ctx['explain'] is True

    def test_explain_false_when_param_absent(self):
        ctx = self._get_context({})
        assert ctx['explain'] is False

    def test_explain_false_when_param_is_other_value(self):
        ctx = self._get_context({'explain': '1'})
        assert ctx['explain'] is False


# ---------------------------------------------------------------------------
# Serializer layer: matchReasons populated / null based on context flag
# ---------------------------------------------------------------------------

def _trial_with_score(factory_kwargs=None):
    """
    Create a Trial via TrialFactory and annotate it with goodness_score so
    TrialSerializer can serialize it (goodness_score is a queryset annotation,
    not a model field).
    """
    from trials.models import Trial
    trial = TrialFactory(**(factory_kwargs or {}))
    return Trial.objects.filter(id=trial.id).with_goodness_score_optimized().first()


def _base_context(extra=None):
    ctx = {
        'patient_info': None,
        'distance_units': 'km',
        'recruitment_status': None,
        'counts': {},
        'template': None,
        'search_type': None,
        'explain': False,
    }
    if extra:
        ctx.update(extra)
    return ctx


class TestExplainSerializer:

    @pytest.mark.django_db
    def test_match_reasons_null_without_explain_flag(self):
        """matchReasons is null when explain=False."""
        trial = _trial_with_score()
        patient_info = PatientInfo(disease='multiple myeloma', patient_age=50)

        serializer = TrialSerializer(
            trial,
            context=_base_context({'explain': False, 'patient_info': patient_info}),
        )
        assert serializer.data['matchReasons'] is None

    @pytest.mark.django_db
    def test_match_reasons_null_without_patient_info(self):
        """matchReasons is null even with explain=True when no patient context."""
        trial = _trial_with_score()

        serializer = TrialSerializer(
            trial,
            context=_base_context({'explain': True, 'patient_info': None}),
        )
        assert serializer.data['matchReasons'] is None

    @pytest.mark.django_db
    def test_match_reasons_populated_with_explain_flag(self):
        """matchReasons is a non-empty list when explain=True and patient_info provided."""
        trial = _trial_with_score({'disease': 'Multiple Myeloma'})
        patient_info = PatientInfo(disease='multiple myeloma', patient_age=50)

        serializer = TrialSerializer(
            trial,
            context=_base_context({'explain': True, 'patient_info': patient_info}),
        )
        reasons = serializer.data['matchReasons']
        assert reasons is not None
        assert isinstance(reasons, list)
        assert len(reasons) > 0
        assert all(r['status'] in {'matched', 'unknown', 'not_matched'} for r in reasons)
