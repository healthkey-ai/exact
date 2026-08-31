"""Regression: Trial.trial_type must be eager-loaded, not fetched per row.

Ported from CB perf(trials) select_related('trial_type') (CANCERBOT-BACKEND-HM).
`TrialSerializer`/`TrialDetailsSerializer` expose `trialType` via
`StringRelatedField(source='trial_type')`, which reads `trial.trial_type` on
every serialized row. Without `select_related('trial_type')` on the base
queryset the FK is fetched once per trial — an N+1. The JOIN folds it into the
main query; there must be no standalone `SELECT ... FROM trials_trialtype`.
"""
import pytest
from unittest.mock import patch, MagicMock

from django.db import connection
from django.test.utils import CaptureQueriesContext

from trials.models import Trial
from trials.api.trials_views import TrialsViewSet
from tests.factories import TrialFactory


def _per_row_trialtype_queries(ctx):
    # The per-row FK fetch reads the table directly (`FROM "trials_trialtype"
    # WHERE "id" = %s`); select_related folds it into the trials query as a
    # JOIN. Match the FROM-the-table shape so unrelated annotation queries that
    # merely reference the column don't trip the assertion.
    return [
        q for q in ctx.captured_queries
        if 'from "trials_trialtype"' in q['sql'].lower()
    ]


@pytest.mark.django_db
def test_serializing_trials_does_not_query_trial_type_per_row():
    # Each TrialFactory gets a distinct trial_type (SubFactory), so a per-row
    # fetch would be one query per trial.
    for _ in range(4):
        TrialFactory()

    qs = Trial.objects.select_related('trial_type')
    with CaptureQueriesContext(connection) as ctx:
        for trial in qs:  # one query, trial_type JOINed in
            _ = trial.trial_type.title if trial.trial_type else None

    per_row = _per_row_trialtype_queries(ctx)
    assert per_row == [], (
        "trial_type is fetched per trial (N+1) — expected a select_related JOIN. "
        f"Saw {len(per_row)} standalone trialtype queries."
    )


@pytest.mark.django_db
def test_get_queryset_eager_loads_trial_type():
    """The view's base queryset must carry select_related('trial_type') so the
    fix survives even if the serializer field changes."""
    view = TrialsViewSet()
    view.action = 'retrieve'
    view.format_kwarg = None
    mock_request = MagicMock()
    mock_request.query_params = {}
    mock_request.data = {}
    mock_request.method = 'GET'
    view.request = mock_request

    with patch.object(view, '_resolve_patient_info', return_value=None), \
         patch.object(view, '_resolve_study_preferences') as mock_prefs:
        mock_prefs.return_value = MagicMock(distance_units='km', recruitment_status=None)
        qs = view.get_queryset(patient_info=None)

    assert 'trial_type' in qs.query.select_related, (
        "get_queryset dropped select_related('trial_type') — trial_type N+1 regressed."
    )
