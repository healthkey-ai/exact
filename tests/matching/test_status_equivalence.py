"""Tests for the SQL-vs-matcher differential-equivalence comparator (#4832).

These guard the comparator TOOL (does it correctly diff the two paths and
attribute the deciding attrs?), not the equivalence CONTRACT itself — the two
paths are known to diverge today, and reconciling them is the #4832 fix. Once
that lands, `test_real_pipeline_*` can be tightened from "runs and is well-formed"
to "returns no divergences".
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.matching import status_equivalence as se
from tests.factories import TrialFactory

VALID = {se.ELIGIBLE, se.POTENTIAL, se.NOT_ELIGIBLE}


def _is_snake(name: str) -> bool:
    return name == name.lower() and ' ' not in name


class TestSqlStatusMap:
    @pytest.mark.django_db
    def test_buckets_are_valid_and_cover_base(self):
        TrialFactory(disease='multiple myeloma')
        TrialFactory(disease='multiple myeloma')
        pi = PatientInfo(disease='multiple myeloma')
        base = Trial.objects.all()

        status = se.sql_status_map(base, pi)

        assert set(status) == set(base.values_list('id', flat=True))
        assert set(status.values()) <= VALID


class TestCompareDiffLogic:
    """Deterministic diff logic, matcher forced via monkeypatch so the test does
    not depend on which real attrs happen to diverge on factory data."""

    @pytest.mark.django_db
    def test_no_divergence_when_paths_agree(self, monkeypatch):
        TrialFactory(disease='multiple myeloma')
        pi = PatientInfo(disease='multiple myeloma')
        base = Trial.objects.all()
        sql = se.sql_status_map(base, pi)

        # Force the matcher to echo the SQL verdict for every trial.
        monkeypatch.setattr(
            se, 'matcher_status',
            lambda trial, patient_info: (sql[trial.id], [], []),
        )
        assert se.compare(base, pi) == []

    @pytest.mark.django_db
    def test_detects_and_attributes_divergence(self, monkeypatch):
        TrialFactory(disease='multiple myeloma')
        pi = PatientInfo(disease='multiple myeloma')
        base = Trial.objects.all()
        sql = se.sql_status_map(base, pi)

        # Force every trial to the opposite matcher verdict with a known cause.
        def fake(trial, patient_info):
            other = se.NOT_ELIGIBLE if sql[trial.id] != se.NOT_ELIGIBLE else se.ELIGIBLE
            return (other, ['some_unknown_attr'], ['meets_crab'])
        monkeypatch.setattr(se, 'matcher_status', fake)

        divs = se.compare(base, pi, attribute_sql_drops=False)

        assert len(divs) == base.count()
        d = divs[0]
        assert d.sql_status in VALID and d.matcher_status in VALID
        assert d.sql_status != d.matcher_status
        assert d.direction == f'{d.sql_status}->{d.matcher_status}'
        assert d.matcher_not_matched_attrs == ['meets_crab']

    @pytest.mark.django_db
    def test_summary_aggregates_direction_and_attrs(self, monkeypatch):
        TrialFactory(disease='multiple myeloma')
        pi = PatientInfo(disease='multiple myeloma')
        base = Trial.objects.all()
        sql = se.sql_status_map(base, pi)
        monkeypatch.setattr(
            se, 'matcher_status',
            lambda trial, patient_info: (
                se.NOT_ELIGIBLE if sql[trial.id] != se.NOT_ELIGIBLE else se.ELIGIBLE,
                [], ['meets_crab'],
            ),
        )
        divs = se.compare(base, pi, attribute_sql_drops=False)

        summary = se.ComparisonSummary()
        summary.add(trials_compared=base.count(), divs=divs)

        assert summary.divergences == len(divs)
        assert summary.trials_compared == base.count()
        assert 0.0 <= summary.rate <= 1.0
        assert sum(summary.by_direction.values()) == len(divs)
        # Every divergence's deciding attr was meets_crab (not_matched wins).
        assert summary.by_attr.get('meets_crab') == len(divs)


class TestRealPipeline:
    @pytest.mark.django_db
    def test_runs_and_is_well_formed_on_real_objects(self):
        """End-to-end on factory data with the real matcher (no monkeypatch).
        Asserts the comparator executes and emits well-formed divergences —
        NOT that there are zero (the paths are known to diverge pre-#4832)."""
        TrialFactory(disease='multiple myeloma')
        TrialFactory(disease='multiple myeloma')
        pi = PatientInfo(disease='multiple myeloma')
        base = Trial.objects.all()

        divs = se.compare(base, pi)

        for d in divs:
            assert d.sql_status in VALID
            assert d.matcher_status in VALID
            assert d.sql_status != d.matcher_status
            assert isinstance(d.matcher_unknown_attrs, list)
            assert isinstance(d.matcher_not_matched_attrs, list)

    @pytest.mark.django_db
    def test_matcher_status_returns_valid_verdict(self):
        trial = TrialFactory(disease='multiple myeloma')
        pi = PatientInfo(disease='multiple myeloma')

        verdict, unknown, not_matched = se.matcher_status(trial, pi)

        assert verdict in VALID
        assert isinstance(unknown, list) and isinstance(not_matched, list)
        # Aggregation rule: not_matched wins, then unknown, else eligible.
        if not_matched:
            assert verdict == se.NOT_ELIGIBLE
        elif unknown:
            assert verdict == se.POTENTIAL
        else:
            assert verdict == se.ELIGIBLE

    @pytest.mark.django_db
    def test_matcher_status_verdict_equals_trial_match_status(self):
        """Faithfulness guard: the comparator's verdict MUST equal the real
        `trial_match_status`. If someone changes the matcher's aggregation or
        disease gate, this fails instead of the tool silently mis-measuring."""
        trials = [
            TrialFactory(disease='multiple myeloma'),
            TrialFactory(disease='multiple myeloma', age_low_limit=70),
            TrialFactory(disease='multiple myeloma', age_high_limit=40),
        ]
        pi = PatientInfo(disease='multiple myeloma', patient_age=65)
        for trial in trials:
            assert se.matcher_status(trial, pi)[0] == \
                UserToTrialAttrMatcher(trial, pi).trial_match_status()


class TestSqlAttribution:
    @pytest.mark.django_db
    def test_dropped_attrs_names_first_disqualifier_in_snake_case(self):
        # Trial requires age >= 70; a 65-yo patient is filtered out on age.
        trial = TrialFactory(disease='multiple myeloma', age_low_limit=70)
        pi = PatientInfo(disease='multiple myeloma', patient_age=65)
        base = Trial.objects.all()

        dropped = se._sql_dropped_attrs(base, trial.id, pi)

        assert 'patient_age' in dropped
        assert all(_is_snake(a) for a in dropped)

    @pytest.mark.django_db
    def test_potential_attrs_are_snake_case(self):
        # Trial constrains age; patient leaves age blank -> age is 'potential'.
        trial = TrialFactory(disease='multiple myeloma', age_low_limit=18)
        pi = PatientInfo(disease='multiple myeloma')
        counts = se._blank_attr_counts(pi)

        potential = se._sql_potential_attrs(trial, counts)

        assert 'patient_age' in potential
        # The whole point of the snake normalization (P2-1): no camelCase leaks.
        assert all(_is_snake(a) for a in potential)
