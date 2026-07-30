"""E1.4 — ExactMatcher (exact_matching.backend) implements CB's MatcherBackend.

Validates the search() seam end-to-end against EXACT's own Trial queryset, and
that the goodness-score weights are read as explicit fields off `prefs`
(decision B — the package is userless, so ExactMatcher passes weight floats, not
a UserProfile).
"""
import pytest

from exact_matching import __version__
from exact_matching.backend import ExactMatcher
from trials.models import Trial
from trials.services.study_preferences import StudyPreferences
from tests.factories import TrialFactory

_WEIGHTS = ("benefit_weight", "patient_burden_weight", "risk_weight", "distance_penalty_weight")


class _Patient:
    """Duck ResolvedPatient — ExactMatcher only calls as_patient_info() / geo_point."""

    def __init__(self, pi=None, geo_point=None):
        self._pi = pi
        self.geo_point = geo_point

    def as_patient_info(self):
        return self._pi


class _Prefs:
    """Duck StudyPrefs; weight fields set only when provided (to exercise defaults)."""

    def __init__(self, search_type="all", **weights):
        self.query_params = {}
        self.study_info = StudyPreferences()
        self.search_type = search_type
        self.recruitment_status = None
        for f, v in weights.items():
            setattr(self, f, v)


def _direct(weights, search_type="all"):
    """Replicate the LocalMatcher call sequence directly, with explicit weights."""
    qs, _ = Trial.objects.filtered_trials(
        search_options={}, study_info=StudyPreferences(),
        patient_info=None, add_traces=False, search_type=search_type,
    )
    return qs.with_goodness_score_optimized(**weights, geo_point=None, recruitment_status=None)


@pytest.mark.django_db
class TestExactMatcherSearch:
    def test_annotates_goodness_score(self):
        TrialFactory()
        TrialFactory()
        qs = ExactMatcher().search(Trial.objects.all(), _Patient(), _Prefs())
        assert "goodness_score" in qs.query.annotations
        assert qs.count() == 2

    def test_explicit_weights_propagate(self):
        TrialFactory()
        TrialFactory()
        weights = dict(benefit_weight=40.0, patient_burden_weight=10.0,
                       risk_weight=30.0, distance_penalty_weight=20.0)
        got = {t.id: t.goodness_score
               for t in ExactMatcher().search(Trial.objects.all(), _Patient(), _Prefs(**weights))}
        exp = {t.id: t.goodness_score for t in _direct(weights)}
        assert got == exp and len(got) == 2

    def test_absent_weights_default_to_equal(self):
        TrialFactory()
        TrialFactory()
        # _Prefs() sets no weight attrs → ExactMatcher's getattr defaults to 25.0 each.
        got = {t.id: t.goodness_score
               for t in ExactMatcher().search(Trial.objects.all(), _Patient(), _Prefs())}
        exp = {t.id: t.goodness_score for t in _direct({f: 25.0 for f in _WEIGHTS})}
        assert got == exp and len(got) == 2

    def test_version_tag(self):
        assert ExactMatcher().version == f"exact-{__version__}"
