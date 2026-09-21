"""
Tests for goodness score weight query parameters on the trials search API.

The view parses benefitWeight, patientBurdenWeight, riskWeight,
distancePenaltyWeight from query params and forwards them to
with_goodness_score_optimized(). These unit tests call get_queryset()
directly with a controlled mock queryset to capture the actual kwargs.
"""
import pytest
from unittest.mock import patch, MagicMock, call

from trials.api.trials_views import TrialsViewSet
from trials.services.utils import (
    normalize_goodness_weights,
    parse_goodness_weights,
)


def _make_view(query_params: dict, action: str = 'list') -> TrialsViewSet:
    """Return a TrialsViewSet instance with a mock request."""
    view = TrialsViewSet()
    view.action = action
    view.format_kwarg = None

    mock_request = MagicMock()
    mock_request.query_params = query_params
    mock_request.data = {}
    mock_request.method = 'GET'
    view.request = mock_request
    return view


def _make_qs():
    """
    A MagicMock that quacks like a chained queryset.

    filtered_trials returns (qs, None) — matching the real signature.
    with_goodness_score_optimized records kwargs and returns self.
    """
    qs = MagicMock()
    # Every chained method returns the same qs
    for method in (
        'filter', 'exclude', 'select_related', 'prefetch_related',
        'with_goodness_score_optimized', 'with_distance_optimized',
        'with_potential_attrs_count', 'order_by', 'annotate', 'all',
    ):
        getattr(qs, method).return_value = qs
    # filtered_trials returns a (queryset, extra) tuple
    qs.filtered_trials.return_value = (qs, None)
    qs.count.return_value = 0
    return qs


def _call_get_queryset(view, qs):
    """
    Patch Trial.objects so get_queryset() uses our mock qs.
    Returns the captured calls to with_goodness_score_optimized.
    """
    with patch('trials.api.trials_views.Trial') as MockTrial:
        MockTrial.objects.all.return_value = qs
        try:
            view.get_queryset()
        except Exception:
            pass  # view may fail later (pagination, serializer, etc.) — we only care about the qs call

    return qs.with_goodness_score_optimized.call_args


class TestGoodnessWeightParams:

    def test_default_weights(self):
        """No weight params → defaults 25/25/25/25."""
        view = _make_view({})
        qs = _make_qs()
        call_args = _call_get_queryset(view, qs)

        assert call_args is not None, 'with_goodness_score_optimized was not called'
        assert call_args.kwargs['benefit_weight'] == 25.0
        assert call_args.kwargs['patient_burden_weight'] == 25.0
        assert call_args.kwargs['risk_weight'] == 25.0
        assert call_args.kwargs['distance_penalty_weight'] == 25.0

    def test_custom_weights_forwarded(self):
        """Custom weight params are parsed and forwarded correctly."""
        view = _make_view({
            'benefitWeight': '40',
            'patientBurdenWeight': '30',
            'riskWeight': '20',
            'distancePenaltyWeight': '10',
        })
        qs = _make_qs()
        call_args = _call_get_queryset(view, qs)

        assert call_args is not None
        assert call_args.kwargs['benefit_weight'] == 40.0
        assert call_args.kwargs['patient_burden_weight'] == 30.0
        assert call_args.kwargs['risk_weight'] == 20.0
        assert call_args.kwargs['distance_penalty_weight'] == 10.0

    def test_invalid_weight_falls_back_alone(self):
        """A weight that cannot be read defaults; the others are kept.

        It used to take them with it — all four were parsed inside one
        `try` — so a single unreadable value silently re-ranked the whole
        search by equal weights, discarding three that had arrived fine.
        """
        view = _make_view({
            'benefitWeight': 'bad-value',
            'patientBurdenWeight': '30',
            'riskWeight': '20',
            'distancePenaltyWeight': '10',
        })
        qs = _make_qs()
        call_args = _call_get_queryset(view, qs)

        assert call_args is not None
        assert call_args.kwargs['benefit_weight'] == 25.0
        assert call_args.kwargs['patient_burden_weight'] == 30.0
        assert call_args.kwargs['risk_weight'] == 20.0
        assert call_args.kwargs['distance_penalty_weight'] == 10.0

    def test_the_neighbours_cost_the_same_whatever_kind_of_bad_it_is(self):
        """What the fix makes equal, and what it leaves unequal.

        Run through BOTH layers — the view's parse and the normalizer it
        feeds — because that is where the answer is finally decided, and a
        view-level assertion would compare two dicts neither layer had
        finished with.

        Equal now: the three readable weights, whichever kind of bad their
        neighbour was. Still unequal, and pinned here rather than papered
        over: the bad field itself, where unreadable means 25 and negative
        means 0 — a 25-point difference from the same "this is nonsense".
        """
        rest = {
            'patientBurdenWeight': '30',
            'riskWeight': '20',
            'distancePenaltyWeight': '10',
        }
        unreadable = normalize_goodness_weights(
            **parse_goodness_weights({'benefitWeight': 'abc', **rest})
        )
        negative = normalize_goodness_weights(
            **parse_goodness_weights({'benefitWeight': '-5', **rest})
        )

        assert unreadable[1:] == negative[1:] == (30.0, 20.0, 10.0)
        assert unreadable[0] == 25.0
        assert negative[0] == 0.0

    def test_partial_weights(self):
        """Only one weight param supplied → rest default to 25.0."""
        view = _make_view({'benefitWeight': '50'})
        qs = _make_qs()
        call_args = _call_get_queryset(view, qs)

        assert call_args is not None
        assert call_args.kwargs['benefit_weight'] == 50.0
        assert call_args.kwargs['patient_burden_weight'] == 25.0
        assert call_args.kwargs['risk_weight'] == 25.0
        assert call_args.kwargs['distance_penalty_weight'] == 25.0

    def test_float_string_parsed(self):
        """Decimal string weights are parsed as floats."""
        view = _make_view({
            'benefitWeight': '33.5',
            'patientBurdenWeight': '16.5',
            'riskWeight': '25.0',
            'distancePenaltyWeight': '25.0',
        })
        qs = _make_qs()
        call_args = _call_get_queryset(view, qs)

        assert call_args is not None
        assert call_args.kwargs['benefit_weight'] == 33.5
        assert call_args.kwargs['patient_burden_weight'] == 16.5

    def test_search_action_also_passes_weights(self):
        """Weights are applied for the 'search' action too."""
        view = _make_view({'benefitWeight': '60'}, action='search')
        qs = _make_qs()
        call_args = _call_get_queryset(view, qs)

        assert call_args is not None
        assert call_args.kwargs['benefit_weight'] == 60.0
