"""OMOP cutover phase 3b: matching reads OMOP concept_id columns behind the flag.

With EXACT_OMOP_THERAPY on, the trial therapy columns the matcher/queryset read
flip to the omop_* concept_id columns AND the patient's internal therapy codes are
translated to the same concept_ids — so matching produces the same result it would
on the legacy columns, given a correct backfill. With the flag off everything is a
pass-through (covered by the rest of the suite staying green).
"""
import pytest
from django.test import override_settings

from trials.models import Trial, Therapy, TherapyComponent, TherapyComponentCategory
from trials.services.omop.patient_therapy_codes import to_match_codes, to_match_value_map
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


# ── patient-code translation helper ──────────────────────────────────

def test_to_match_codes_passthrough_when_flag_off():
    Therapy.objects.create(code='zz_vrd', title='zz VRd', omop_concept_id=111)
    assert to_match_codes(Therapy, ['zz_vrd']) == ['zz_vrd']


@override_settings(EXACT_OMOP_THERAPY=True)
def test_to_match_codes_translates_when_flag_on():
    Therapy.objects.create(code='zz_vrd', title='zz VRd', omop_concept_id=111)
    Therapy.objects.create(code='zz_td', title='zz Td', omop_concept_id=222)
    Therapy.objects.create(code='zz_unmapped', title='zz Unmapped', omop_concept_id=None)
    # mapped -> concept_id strings (sorted, deduped); unmapped dropped
    assert to_match_codes(Therapy, ['zz_td', 'zz_vrd', 'zz_unmapped']) == ['111', '222']


@override_settings(EXACT_OMOP_THERAPY=True)
def test_to_match_codes_preserves_none_and_empty():
    # None (unknown) and [] (no codes) must pass through so the matcher's
    # unknown-vs-no-match logic is preserved.
    assert to_match_codes(Therapy, None) is None
    assert to_match_codes(Therapy, []) == []


@override_settings(EXACT_OMOP_THERAPY=True)
def test_to_match_value_map_rekeys_to_concept_ids():
    TherapyComponent.objects.create(code='zz_bort', title='Bortezomib', omop_concept_id=333)
    TherapyComponent.objects.create(code='zz_nomap', title='Unmapped', omop_concept_id=None)
    out = to_match_value_map(TherapyComponent, {'zz_bort': 'Bortezomib', 'zz_nomap': 'Unmapped'})
    assert out == {'333': 'Bortezomib'}  # unmapped dropped, key is concept_id


def test_to_match_value_map_passthrough_when_flag_off():
    m = {'zz_bort': 'Bortezomib'}
    assert to_match_value_map(TherapyComponent, m) == m


# ── queryset matching parity (legacy vs OMOP columns) ────────────────

def _two_trials():
    Therapy.objects.create(code='zz_vrd', title='zz VRd', omop_concept_id=111)
    Therapy.objects.create(code='zz_other', title='zz Other', omop_concept_id=999)
    # A requires the patient's therapy; B requires a different one. Both legacy and
    # omop columns populated consistently (as a correct backfill would leave them).
    a = TrialFactory(therapies_required=['zz_vrd'], omop_therapies_required=['111'])
    b = TrialFactory(therapies_required=['zz_other'], omop_therapies_required=['999'])
    return a, b


def test_queryset_matches_on_legacy_codes_when_flag_off():
    a, b = _two_trials()
    ids = set(Trial.objects.eligible_for_therapy_from_lines(['zz_vrd']).values_list('id', flat=True))
    assert a.id in ids
    assert b.id not in ids


@override_settings(EXACT_OMOP_THERAPY=True)
def test_queryset_matches_on_omop_concept_ids_when_flag_on():
    a, b = _two_trials()
    # Patient passes the INTERNAL code; the queryset translates it to concept_id
    # 111 and overlaps against omop_therapies_required. Same selection as legacy.
    ids = set(Trial.objects.eligible_for_therapy_from_lines(['zz_vrd']).values_list('id', flat=True))
    assert a.id in ids
    assert b.id not in ids


@override_settings(EXACT_OMOP_THERAPY=True)
def test_queryset_excludes_via_omop_excluded_column():
    Therapy.objects.create(code='zz_vrd', title='zz VRd', omop_concept_id=111)
    excl = TrialFactory(therapies_excluded=['zz_vrd'], omop_therapies_excluded=['111'])
    qs = Trial.objects.eligible_for_required_and_excluded_lists(
        values=['111'],
        required_attr_name='omop_therapies_required',
        excluded_attr_name='omop_therapies_excluded',
    )
    assert excl.id not in set(qs.values_list('id', flat=True))


# ── matcher decision parity (the riskiest path: derive-then-translate) ─

def _matcher_trials():
    Therapy.objects.create(code='zz_vrd', title='zz VRd', omop_concept_id=111)
    # legacy + omop columns populated consistently, as a correct backfill leaves them
    match = TrialFactory(therapies_required=['zz_vrd'], omop_therapies_required=['111'])
    other = TrialFactory(therapies_required=['zz_other'], omop_therapies_required=['999'])
    return match, other


def test_matcher_decision_on_legacy_codes_when_flag_off():
    match, other = _matcher_trials()
    pi = PatientInfo(disease='multiple myeloma')
    assert UserToTrialAttrMatcher(match, pi)._match_therapy_related_things(['zz_vrd'], False) == 'matched'
    assert UserToTrialAttrMatcher(other, pi)._match_therapy_related_things(['zz_vrd'], False) == 'not_matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_matcher_decision_on_omop_concept_ids_when_flag_on():
    # Same patient internal code, same matched/not_matched outcome as legacy —
    # the matcher translates ['zz_vrd'] -> ['111'] and compares against the
    # trial's omop_therapies_required. Proves derive-then-translate parity.
    match, other = _matcher_trials()
    pi = PatientInfo(disease='multiple myeloma')
    assert UserToTrialAttrMatcher(match, pi)._match_therapy_related_things(['zz_vrd'], False) == 'matched'
    assert UserToTrialAttrMatcher(other, pi)._match_therapy_related_things(['zz_vrd'], False) == 'not_matched'
