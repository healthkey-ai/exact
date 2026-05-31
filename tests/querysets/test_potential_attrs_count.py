"""Tests for TrialQuerySet.add_potential_attrs_count / with_potential_attrs_count
after the .extra() → annotate(RawSQL) port (#97).

Pre-port the method used `.extra(select={...})` for the three annotations
and `.extra(where=[...])` for the `eligible`/`potential` filters. Post-port
it uses `annotate(RawSQL(...))` and `filter(potential_attrs_count={0|__gt:0})`
referencing the annotation. SQL emitted is functionally identical; this
test locks the surface so a future regression in the annotation names or
filter semantics shows up at unit level rather than via downstream
serializer flakiness.
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from tests.factories import TrialFactory


class TestAddPotentialAttrsCount:
    @pytest.mark.django_db
    def test_annotations_attached_with_empty_patient_info(self):
        """The patient_info=None branch passes attributes=[] through
        `add_potential_attrs_count`, so the annotations must still attach
        even when the CASE-WHEN list is degenerate. Each annotated value
        comes from `num_nonnulls(NULL) = 0`.
        """
        TrialFactory()
        qs = Trial.objects.all().with_potential_attrs_count(patient_info=None)
        trial = qs.first()
        assert hasattr(trial, 'potential_attrs_count')
        assert hasattr(trial, 'match_score')
        assert hasattr(trial, 'potential_profit_avg')
        # `num_nonnulls(NULL)` is 0; integer division 0*100 / 0 → SQL error
        # in some engines, but Postgres returns NULL when divisor is 0
        # via the `num_nonnulls` path. Either way the value should be
        # int-or-None — not crash.
        assert trial.potential_attrs_count in (0, None)

    @pytest.mark.django_db
    def test_search_type_eligible_filters_to_zero(self):
        """search_type='eligible' must keep only trials where
        potential_attrs_count == 0. With patient_info=None the candidate
        list is empty, so every trial trivially scores 0 → all pass.
        """
        TrialFactory()
        TrialFactory()
        qs = Trial.objects.all().with_potential_attrs_count(
            patient_info=None, search_type='eligible'
        )
        assert qs.count() == 2

    @pytest.mark.django_db
    def test_search_type_potential_filters_to_positive(self):
        """search_type='potential' must keep only trials where
        potential_attrs_count > 0. With patient_info=None every trial
        scores 0, so none pass — this asserts the filter is wired the
        right direction (no off-by-one or inverted comparison).
        """
        TrialFactory()
        TrialFactory()
        qs = Trial.objects.all().with_potential_attrs_count(
            patient_info=None, search_type='potential'
        )
        assert qs.count() == 0

    @pytest.mark.django_db
    def test_match_score_accessible_with_real_patient_info(self):
        """End-to-end with a real PatientInfo: the matcher pipeline produces
        a non-empty candidate list, so the annotations are emitted from
        non-trivial SQL. Just smoke-test that the annotation lookup works.
        """
        TrialFactory(disease='multiple myeloma')
        pi = PatientInfo(disease='multiple myeloma')
        qs = Trial.objects.all().with_potential_attrs_count(patient_info=pi)
        trial = qs.first()
        # match_score is a non-negative integer (0..100) computed by
        # `num_nonnulls(filled) * 100 / num_nonnulls(all)`. The actual
        # value depends on the matcher's bookkeeping for this patient;
        # we only care that the attribute is reachable and well-typed.
        assert hasattr(trial, 'match_score')
        ms = trial.match_score
        assert ms is None or isinstance(ms, int)
