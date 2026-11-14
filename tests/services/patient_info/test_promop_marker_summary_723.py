"""PROMOP's molecular-marker summary is read as unknown, not refused (#723 stopgap).

PROMOP summarises present findings as `"TP53: R175H; KMT2D: Q3912*"`. The strict
marker check (80a934b) needs every token to be a marker code, so it refused the
whole patient: HTTP 422 from `/normalize-promop-row/`, and HealthTree's Find
Trials showed "Couldn't load your health profile". The summary is now read as
unknown. These tests pin that it stays unknown downstream, never a "no".
"""
from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient

from tests.factories import TrialFactory
from trials.services.patient_info.promop_adapter import (
    UnsupportedPatientVocabulary,
    _is_promop_findings_summary,
    build_patient_info_from_promop_row,
    normalize_promop_row,
)
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes

SUMMARIES = [
    'TP53: R175H', 'TP53: R175H; KMT2D: Q3912*', 'NOTCH1: p.P2514fs ;  CCND1: amplification',
    'CCND1/IGH: t(11;14)',                 # a translocation: `;` inside brackets
    'CCND1/IGH: t(11;14); TP53: R175H',
    'TP53: ',                              # an empty variant
    'BRAF: c.[1799T>A;1800G>A]',           # an HGVS variant with `;` in brackets
]


class TestShape:
    @pytest.mark.parametrize('value', SUMMARIES)
    def test_promop_summaries_are_recognised(self, value):
        assert _is_promop_findings_summary(value)

    @pytest.mark.parametrize('value', [
        'tp53Mutation', 'TP53 Mutation', 'tp53Mutation, kmt2dMutation',
        't(11;14) Translocation',          # a real title with a semicolon
        'TP53: R175H; tp53Mutation',       # mixed: not purely a summary
        'tp53Mutation, x: y',              # a code list with a stray colon
        ': R175H', 'none', '', None, 7,
    ])
    def test_anything_else_is_not(self, value):
        assert not _is_promop_findings_summary(value)


@pytest.mark.django_db
class TestNormalization:
    @pytest.mark.parametrize('value', SUMMARIES)
    def test_a_summary_becomes_unknown_instead_of_refusing_the_patient(self, value):
        row = normalize_promop_row({'disease': 'mantle cell lymphoma', 'molecular_markers': value})
        assert row['molecular_markers'] == ''

    @pytest.mark.parametrize('value', [
        'TP53 Mutation, unsupported-private-token', 'unsupported-private-token',
        'TP53: R175H; tp53Mutation',
    ])
    def test_unsupported_values_are_still_refused(self, value):
        with pytest.raises(UnsupportedPatientVocabulary):
            normalize_promop_row({'molecular_markers': value})

    def test_real_codes_still_resolve(self):
        assert normalize_promop_row({'molecular_markers': 'tp53Mutation'})['molecular_markers'] == 'tp53Mutation'

    def test_over_http_it_is_a_200_not_a_422(self):
        client = APIClient()
        client.force_authenticate(user=SimpleNamespace(is_authenticated=True, pk=9723))
        response = client.post('/normalize-promop-row/', {
            'disease': 'mantle cell lymphoma',
            'molecular_markers': 'TP53: R175H; KMT2D: Q3912*',
        }, format='json')
        assert response.status_code == 200, response.content[:300]


@pytest.mark.django_db
class TestItStaysUnknownDownstream:
    """The clinical-safety half: losing the summary must never read as "no"."""

    def _patient(self):
        return build_patient_info_from_promop_row({
            'disease': 'mantle cell lymphoma',
            'molecular_markers': 'TP53: R175H; KMT2D: Q3912*',
            'tp53_disruption': True,   # PROMOP's own aggregate, from the findings
        })

    def test_markers_are_blank_and_tp53_is_kept(self):
        patient = self._patient()
        attrs = PatientInfoAttributes(patient)
        assert attrs.is_attr_blank('molecular_markers')
        assert patient.tp53_disruption is True
        assert {'tp53_mutation', 'kmt2d_mutation'} <= set(
            attrs.high_risk_mcl_criteria_unknown_codes(['tp53_mutation', 'kmt2d_mutation'])
        )

    def test_without_promops_aggregate_tp53_is_unknown_not_false(self):
        # The dropped summary may name TP53. With no `tp53_disruption` in the
        # row, the derivation would have answered a confirmed False.
        patient = build_patient_info_from_promop_row({
            'disease': 'mantle cell lymphoma',
            'molecular_markers': 'TP53: R175H',
        })
        assert PatientInfoAttributes(patient).tp53_disruption is None

    @pytest.mark.parametrize('aggregate', [True, False])
    def test_promops_own_aggregate_is_kept_when_tp53_is_not_in_the_summary(self, aggregate):
        patient = build_patient_info_from_promop_row({
            'disease': 'mantle cell lymphoma',
            'molecular_markers': 'KMT2D: Q3912*',
            'tp53_disruption': aggregate,
        })
        assert PatientInfoAttributes(patient).tp53_disruption is aggregate

    @pytest.mark.parametrize('summary', [
        'TP53: R175H', '17p: del(17p)', 'KMT2D: x; tp53: y',
        'Chromosome 17: monosomy', 'del(17)(p13): loss',
    ])
    def test_a_false_that_the_summary_contradicts_becomes_unknown(self, summary):
        # PROMOP answers False unless a TP53 finding is strictly `pathogenic`;
        # the summary lists the finding anyway. Unknown, not a confirmed "no".
        patient = build_patient_info_from_promop_row({
            'disease': 'mantle cell lymphoma',
            'molecular_markers': summary,
            'tp53_disruption': False,
        })
        assert PatientInfoAttributes(patient).tp53_disruption is None

    @pytest.mark.parametrize('surrogate', [{'p53_ihc': 60}, {'p53_ihc': '75'}, {'cytogenetic_markers': 'del17p'}])
    def test_with_a_surrogate_the_contradiction_still_answers_unknown(self, surrogate):
        # Nulling the False here would let the surrogate make it a confirmed
        # True; keeping it lets `_tp53_contradiction` answer unknown.
        patient = build_patient_info_from_promop_row({
            'disease': 'mantle cell lymphoma',
            'molecular_markers': 'TP53: R175H',
            'tp53_disruption': False,
            **surrogate,
        })
        assert PatientInfoAttributes(patient).tp53_disruption is None

    def test_a_true_stays_true(self):
        patient = build_patient_info_from_promop_row({
            'disease': 'mantle cell lymphoma',
            'molecular_markers': '17p: del(17p)',
            'tp53_disruption': True,
        })
        assert PatientInfoAttributes(patient).tp53_disruption is True

    def test_a_trial_excluding_the_marker_is_not_eligible(self):
        trial = TrialFactory(disease='mantle cell lymphoma', molecular_markers_excluded=['tp53Mutation'])
        matcher = UserToTrialAttrMatcher(trial, self._patient())
        assert matcher.attr_match_status('molecular_markers') == 'unknown'
        assert matcher.trial_match_status() == 'potential'
