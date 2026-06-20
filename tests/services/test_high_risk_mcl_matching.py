"""
High-risk MCL criteria MATCHING (#185, CB #4399-#4437).

Covers the queryset eligibility filter, the criteria_count aggregate matcher
(required/min_count + sufficient_any + excluded, unknown-vs-none), and the
per-criterion breakdown.
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.normalize import normalize_patient_info
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory


def _mcl_patient(**kwargs):
    """MCL patient with derived high_risk_mcl_criteria computed via normalize."""
    pi = PatientInfo(disease='mantle cell lymphoma', **kwargs)
    normalize_patient_info(pi)
    return pi


def _status(trial, pi):
    return UserToTrialAttrMatcher(trial=trial, patient_info=pi).attr_match_status('high_risk_mcl_criteria')


class TestHighRiskMclQueryset:
    @pytest.mark.django_db
    def test_required_overlap_includes_and_disjoint_excludes(self):
        t_no_gate = TrialFactory(disease='mantle cell lymphoma')
        t_req = TrialFactory(disease='mantle cell lymphoma', high_risk_mcl_criteria_required=['tp53_mutation'])
        t_other = TrialFactory(disease='mantle cell lymphoma', high_risk_mcl_criteria_required=['del17p'])

        result = set(
            Trial.objects.all()
            .eligible_for_high_risk_mcl_criteria(['tp53_mutation'])
            .values_list('id', flat=True)
        )
        assert t_no_gate.id in result, 'no inclusion gate -> always eligible'
        assert t_req.id in result, 'required overlaps patient -> eligible'
        assert t_other.id not in result, 'required disjoint from patient -> dropped'

    @pytest.mark.django_db
    def test_sufficient_any_includes(self):
        t = TrialFactory(
            disease='mantle cell lymphoma',
            high_risk_mcl_criteria_required=['tp53_mutation', 'del17p'],
            high_risk_mcl_criteria_sufficient_any=['blastoid'],
        )
        result = set(
            Trial.objects.all()
            .eligible_for_high_risk_mcl_criteria(['blastoid'])
            .values_list('id', flat=True)
        )
        assert t.id in result, 'sufficient_any overlap alone qualifies'

    @pytest.mark.django_db
    def test_excluded_drops_trial(self):
        t = TrialFactory(disease='mantle cell lymphoma', high_risk_mcl_criteria_excluded=['del17p'])
        result = set(
            Trial.objects.all()
            .eligible_for_high_risk_mcl_criteria(['del17p'])
            .values_list('id', flat=True)
        )
        assert t.id not in result

    @pytest.mark.django_db
    def test_empty_criteria_is_noop(self):
        t = TrialFactory(disease='mantle cell lymphoma', high_risk_mcl_criteria_required=['tp53_mutation'])
        result = set(Trial.objects.all().eligible_for_high_risk_mcl_criteria([]).values_list('id', flat=True))
        assert t.id in result


class TestHighRiskMclAggregateMatcher:
    @pytest.mark.django_db
    def test_matched_when_required_met(self):
        t = TrialFactory(disease='mantle cell lymphoma', high_risk_mcl_criteria_required=['tp53_mutation'])
        pi = _mcl_patient(molecular_markers='tp53Mutation')
        assert _status(t, pi) == 'matched'

    @pytest.mark.django_db
    def test_not_matched_when_required_absent_but_source_known(self):
        # molecular_markers answered (no tp53) -> tp53 confirmed absent.
        t = TrialFactory(disease='mantle cell lymphoma', high_risk_mcl_criteria_required=['tp53_mutation'])
        pi = _mcl_patient(molecular_markers='ccnd1Alteration')
        assert _status(t, pi) == 'not_matched'

    @pytest.mark.django_db
    def test_unknown_when_required_source_blank(self):
        # molecular_markers unanswered -> tp53 undeterminable.
        t = TrialFactory(disease='mantle cell lymphoma', high_risk_mcl_criteria_required=['tp53_mutation'])
        pi = _mcl_patient()
        assert _status(t, pi) == 'unknown'

    @pytest.mark.django_db
    def test_min_count_gate(self):
        t = TrialFactory(
            disease='mantle cell lymphoma',
            high_risk_mcl_criteria_required=['tp53_mutation', 'del17p'],
            high_risk_mcl_criteria_min_count=2,
        )
        # del17p sources are molecular_markers AND cytogenic_markers; both must
        # be answered for it to read confirmed-absent rather than unknown.
        one = _mcl_patient(molecular_markers='tp53Mutation', cytogenic_markers='hyperdiploidy')
        assert _status(t, one) == 'not_matched'
        both = _mcl_patient(molecular_markers='tp53Mutation,del17p13')
        assert _status(t, both) == 'matched'

    @pytest.mark.django_db
    def test_sufficient_any_overrides_required(self):
        t = TrialFactory(
            disease='mantle cell lymphoma',
            high_risk_mcl_criteria_required=['tp53_mutation', 'del17p'],
            high_risk_mcl_criteria_min_count=2,
            high_risk_mcl_criteria_sufficient_any=['blastoid'],
        )
        pi = _mcl_patient(morphologic_variant='blastoid', molecular_markers='ccnd1Alteration')
        assert _status(t, pi) == 'matched'

    @pytest.mark.django_db
    def test_excluded_present_is_not_matched(self):
        t = TrialFactory(disease='mantle cell lymphoma', high_risk_mcl_criteria_excluded=['blastoid'])
        pi = _mcl_patient(morphologic_variant='blastoid')
        assert _status(t, pi) == 'not_matched'

    @pytest.mark.django_db
    def test_no_criteria_is_matched(self):
        t = TrialFactory(disease='mantle cell lymphoma')
        pi = _mcl_patient(molecular_markers='tp53Mutation')
        assert _status(t, pi) == 'matched'


class TestHighRiskMclBreakdown:
    @pytest.mark.django_db
    def test_breakdown_none_when_no_criteria(self):
        t = TrialFactory(disease='mantle cell lymphoma')
        pi = _mcl_patient()
        matcher = UserToTrialAttrMatcher(trial=t, patient_info=pi)
        assert matcher.high_risk_mcl_criteria_breakdown() is None

    @pytest.mark.django_db
    def test_breakdown_per_criterion_status(self):
        t = TrialFactory(
            disease='mantle cell lymphoma',
            high_risk_mcl_criteria_required=['tp53_mutation', 'del17p'],
            high_risk_mcl_criteria_excluded=['blastoid'],
            high_risk_mcl_criteria_min_count=2,
        )
        # tp53 present; del17p known-absent (both marker sources answered);
        # blastoid present (excluded).
        pi = _mcl_patient(molecular_markers='tp53Mutation', cytogenic_markers='hyperdiploidy',
                          morphologic_variant='blastoid')
        bd = UserToTrialAttrMatcher(trial=t, patient_info=pi).high_risk_mcl_criteria_breakdown()
        assert bd['minCount'] == 2
        assert bd['matchedCount'] == 1
        req = {c['code']: c['status'] for c in bd['required']}
        assert req['tp53_mutation'] == 'matched'
        assert req['del17p'] == 'not_matched'
        excl = {c['code']: c['status'] for c in bd['excluded']}
        # excluded criterion present -> not_matched (inverse logic)
        assert excl['blastoid'] == 'not_matched'
        assert bd['aggregate'] == 'not_matched'
