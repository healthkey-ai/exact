"""OMOP cutover phase 3c: matching is a DIRECT concept_id overlap, no translation.

EXACT is stateless and owns no patient-side crosswalk: when EXACT_OMOP_THERAPY is
on, the patient's therapies arrive already as OMOP concept_ids (the consumer /
PROMOP supplies them, pre-expanded), and the trial omop_* columns also hold
concept_ids. Matching is a plain overlap — both sides simply speak the vocabulary
the active profile selects. Off: both sides speak internal codes. There is NO
EXACT-side translation of patient codes (that was phase 3b, reverted here).
"""
import pytest
from django.test import override_settings

from trials.models import Trial, Therapy
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


# ── queryset matching: direct overlap, both vocabularies ─────────────

def _two_trials():
    # A requires one therapy, B another; legacy + omop columns populated
    # consistently (as a correct backfill leaves them).
    a = TrialFactory(therapies_required=['zz_vrd'], omop_therapies_required=['111'])
    b = TrialFactory(therapies_required=['zz_other'], omop_therapies_required=['999'])
    return a, b


@override_settings(EXACT_OMOP_THERAPY=False)
def test_queryset_matches_on_legacy_codes_when_flag_off():
    a, b = _two_trials()
    # Off: patient speaks internal codes (what the legacy columns hold).
    ids = set(Trial.objects.eligible_for_therapy_from_lines(['zz_vrd']).values_list('id', flat=True))
    assert a.id in ids
    assert b.id not in ids


@override_settings(EXACT_OMOP_THERAPY=True)
def test_queryset_matches_on_omop_concept_ids_when_flag_on():
    a, b = _two_trials()
    # On: patient arrives already as OMOP concept_ids; direct overlap against the
    # omop_* column. No translation — the patient passes '111', not 'zz_vrd'.
    ids = set(Trial.objects.eligible_for_therapy_from_lines(['111']).values_list('id', flat=True))
    assert a.id in ids
    assert b.id not in ids


@override_settings(EXACT_OMOP_THERAPY=True)
def test_queryset_omop_internal_code_does_not_match_concept_column():
    # Sanity: an internal code is NOT silently translated. Passing 'zz_vrd' while
    # the column holds concept_ids must NOT match A — the patient must speak OMOP.
    a, _ = _two_trials()
    ids = set(Trial.objects.eligible_for_therapy_from_lines(['zz_vrd']).values_list('id', flat=True))
    assert a.id not in ids


@override_settings(EXACT_OMOP_THERAPY=True)
def test_queryset_excludes_via_omop_excluded_column():
    excl = TrialFactory(therapies_excluded=['zz_vrd'], omop_therapies_excluded=['111'])
    ids = set(Trial.objects.eligible_for_therapy_from_lines(['111']).values_list('id', flat=True))
    assert excl.id not in ids


# ── matcher decision: direct overlap, both vocabularies ──────────────

def _matcher_trials():
    match = TrialFactory(therapies_required=['zz_vrd'], omop_therapies_required=['111'])
    other = TrialFactory(therapies_required=['zz_other'], omop_therapies_required=['999'])
    return match, other


@override_settings(EXACT_OMOP_THERAPY=False)
def test_matcher_decision_on_legacy_codes_when_flag_off():
    match, other = _matcher_trials()
    pi = PatientInfo(disease='multiple myeloma')
    assert UserToTrialAttrMatcher(match, pi)._match_therapy_related_things(['zz_vrd'], False) == 'matched'
    assert UserToTrialAttrMatcher(other, pi)._match_therapy_related_things(['zz_vrd'], False) == 'not_matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_matcher_decision_on_omop_concept_ids_when_flag_on():
    # Patient arrives as concept_ids; matcher compares directly against the
    # trial's omop_therapies_required. No translation.
    match, other = _matcher_trials()
    pi = PatientInfo(disease='multiple myeloma')
    assert UserToTrialAttrMatcher(match, pi)._match_therapy_related_things(['111'], False) == 'matched'
    assert UserToTrialAttrMatcher(other, pi)._match_therapy_related_things(['111'], False) == 'not_matched'
