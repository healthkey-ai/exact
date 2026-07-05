"""OMOP therapy matching with real HemOnc / RxNorm concept_ids from the local vocab.

These tests use the actual concept_ids from the EXACT Therapy + TherapyComponent tables
(populated via load_therapy_omop_concept_ids) so a vocab mapping error is caught here,
not only in production.

Regimen concept_ids (HemOnc):
    VRd       35806260  (Bortezomib + Lenalidomide + Dexamethasone)
    KRd       35806284  (Carfilzomib + Lenalidomide + Dexamethasone)
    Dara-VRd  911993
    Daratumumab mono  35806063
    Bortezomib mono   35804520
    Lenalidomide mono 35803596
    Carfilzomib mono  35806280

Component concept_ids (RxNorm):
    Bortezomib    1336825
    Lenalidomide  19026972
    Carfilzomib   42873638
    Dexamethasone 1518254
    Daratumumab   35605744

Type codes (CB-internal, not OMOP):
    proteasome_inhibitor
    immunomodulatory_drug_(imid)
    corticosteroid
    monoclonal_antibody_(anti_cd38)
"""
import pytest
from django.test import override_settings

from trials.models import Trial
from trials.services.omop.therapy_graph import derive_component_and_type_values
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def enable_omop(settings):
    settings.EXACT_OMOP_THERAPY = True


@pytest.fixture(autouse=True)
def omop_vocab(db):
    """Minimal vocab graph for these tests — no CSV loader needed.

    Sets omop_concept_id on the seeded Therapy/TherapyComponent rows and wires
    up the connections required by derive_component_and_type_values.
    """
    from trials.models import (
        Therapy, TherapyComponent, TherapyComponentCategory,
        TherapyComponentConnection, TherapyComponentCategoryConnection,
    )

    # ── regimen concept_ids ──────────────────────────────────────────────────
    for code, cid in [
        ('vrd', 35806260), ('krd', 35806284), ('dara_vrd', 911993),
        ('daratumumab', 35806063), ('bortezomib', 35804520),
        ('lenalidomide', 35803596), ('carfilzomib', 35806280),
    ]:
        Therapy.objects.filter(code=code).update(omop_concept_id=cid)

    # ── component concept_ids ────────────────────────────────────────────────
    for code, cid in [
        ('bortezomib',   1336825),
        ('lenalidomide', 19026972),
        ('carfilzomib',  42873638),
        ('dexamethasone', 1518254),
        ('daratumumab',  35605744),
    ]:
        TherapyComponent.objects.filter(code=code).update(omop_concept_id=cid)

# ── real concept_ids (from local vocab — test will fail if mapping drifts) ──

VRD   = '35806260'
KRD   = '35806284'
DARA_VRD = '911993'
DARA_MONO = '35806063'
BORT_MONO = '35804520'
LEN_MONO  = '35803596'
CARF_MONO = '35806280'

BORT = '1336825'    # bortezomib component
LEN  = '19026972'   # lenalidomide component
CARF = '42873638'   # carfilzomib component
DEX  = '1518254'    # dexamethasone component
DARA = '35605744'   # daratumumab component

PI_CAT_BORT = 'proteasome_inhibitor'
PI_CAT_LEN  = 'immunomodulatory_drug_(imid)'
PI_CAT_DEX  = 'corticosteroid'
PI_CAT_DARA = 'monoclonal_antibody_(anti_cd38)'


# ── component/type derivation from real vocab ────────────────────────────────

def test_vrd_derives_correct_components():
    comps, types = derive_component_and_type_values([VRD])
    assert sorted(comps) == sorted([BORT, LEN, DEX])
    assert PI_CAT_BORT in types
    assert PI_CAT_LEN in types
    assert PI_CAT_DEX in types


def test_krd_derives_correct_components():
    comps, types = derive_component_and_type_values([KRD])
    assert sorted(comps) == sorted([CARF, LEN, DEX])
    assert PI_CAT_BORT in types   # carfilzomib is also a proteasome inhibitor
    assert PI_CAT_LEN in types
    assert PI_CAT_DEX in types


def test_dara_vrd_derives_correct_components():
    comps, types = derive_component_and_type_values([DARA_VRD])
    assert BORT in comps
    assert LEN in comps
    assert DEX in comps
    assert DARA in comps
    assert PI_CAT_DARA in types


def test_multiple_lines_derive_union_of_components():
    # Patient had VRd 1st line, KRd 2nd line → union of all components
    comps, types = derive_component_and_type_values([VRD, KRD])
    assert BORT in comps
    assert CARF in comps
    assert LEN in comps
    assert DEX in comps


def test_unknown_concept_id_returns_none():
    assert derive_component_and_type_values(['99999999']) == (None, None)


# ── regimen-level exclusion / requirement (omop_therapies_* columns) ─────────

def _mm_pi(*therapy_concept_ids):
    pi = PatientInfo(disease='multiple myeloma')
    lines = list(therapy_concept_ids)
    if lines:
        pi.first_line_therapy = lines[0]
    if len(lines) > 1:
        pi.second_line_therapy = lines[1]
    if len(lines) > 2:
        pi.later_therapies = [{'therapy': c} for c in lines[2:]]
    return pi


def test_trial_excluding_vrd_blocks_patient_with_vrd_first_line():
    trial = TrialFactory(disease='multiple myeloma', omop_therapies_excluded=[VRD])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'not_matched'


def test_trial_requiring_vrd_matches_patient_with_vrd():
    trial = TrialFactory(disease='multiple myeloma', omop_therapies_required=[VRD])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'matched'


def test_trial_requiring_vrd_not_matched_for_krd_only_patient():
    trial = TrialFactory(disease='multiple myeloma', omop_therapies_required=[VRD])
    pi = _mm_pi(KRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([KRD], False)
    assert result == 'not_matched'


def test_trial_excluding_krd_blocks_patient_with_vrd_krd():
    trial = TrialFactory(disease='multiple myeloma', omop_therapies_excluded=[KRD])
    pi = _mm_pi(VRD, KRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD, KRD], False)
    assert result == 'not_matched'


def test_trial_excluding_later_line_blocks_patient_who_had_it():
    trial = TrialFactory(disease='multiple myeloma', omop_therapies_excluded=[DARA_MONO])
    pi = _mm_pi(VRD, KRD, DARA_MONO)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD, KRD, DARA_MONO], False)
    assert result == 'not_matched'


# ── component-level exclusion (omop_therapy_components_* columns) ────────────

def test_bortezomib_naive_trial_excludes_vrd_patient():
    # Trial excludes anyone who had bortezomib (a VRd component)
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_excluded=[BORT])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'not_matched'


def test_bortezomib_naive_trial_allows_krd_patient():
    # KRd does NOT contain bortezomib → patient is bortezomib-naive
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_excluded=[BORT])
    pi = _mm_pi(KRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([KRD], False)
    assert result == 'matched'


def test_trial_requiring_lenalidomide_exposure_matches_vrd_patient():
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[LEN])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'matched'


def test_carfilzomib_naive_trial_allows_vrd_only_patient():
    # VRd has no carfilzomib → patient qualifies
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_excluded=[CARF])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'matched'


def test_daratumumab_naive_trial_excludes_dara_vrd_patient():
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_excluded=[DARA])
    pi = _mm_pi(DARA_VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([DARA_VRD], False)
    assert result == 'not_matched'


# ── extra mapping: HemOnc component concept_id → CB category code ────────────
# These pin the TherapyComponent.omop_concept_id → TherapyComponentCategory.code
# path so a vocab-load error is caught at this level, not only end-to-end.

def test_bortezomib_component_concept_id_maps_to_proteasome_inhibitor():
    _, types = derive_component_and_type_values([BORT_MONO])
    assert PI_CAT_BORT in types


def test_carfilzomib_component_concept_id_also_maps_to_proteasome_inhibitor():
    _, types = derive_component_and_type_values([CARF_MONO])
    assert PI_CAT_BORT in types


def test_lenalidomide_component_concept_id_maps_to_imid():
    _, types = derive_component_and_type_values([LEN_MONO])
    assert PI_CAT_LEN in types


def test_daratumumab_component_concept_id_maps_to_anti_cd38():
    _, types = derive_component_and_type_values([DARA_MONO])
    assert PI_CAT_DARA in types


# ── type-level matching (CB category codes, legacy column) ───────────────────

def test_trial_requiring_proteasome_inhibitor_matches_vrd_patient():
    trial = TrialFactory(disease='multiple myeloma', therapy_types_required=[PI_CAT_BORT])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'matched'


def test_trial_excluding_imid_blocks_vrd_patient():
    trial = TrialFactory(disease='multiple myeloma', therapy_types_excluded=[PI_CAT_LEN])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'not_matched'


def test_trial_requiring_anti_cd38_not_matched_by_vrd_patient():
    # VRd has no anti-CD38 component
    trial = TrialFactory(disease='multiple myeloma', therapy_types_required=[PI_CAT_DARA])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'not_matched'


def test_trial_requiring_anti_cd38_matches_dara_vrd_patient():
    trial = TrialFactory(disease='multiple myeloma', therapy_types_required=[PI_CAT_DARA])
    pi = _mm_pi(DARA_VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([DARA_VRD], False)
    assert result == 'matched'


def test_trial_excluding_anti_cd38_allows_vrd_patient():
    # VRd has no anti-CD38 → patient is eligible despite the exclusion
    trial = TrialFactory(disease='multiple myeloma', therapy_types_excluded=[PI_CAT_DARA])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'matched'


def test_trial_requiring_carfilzomib_component_not_matched_by_vrd_patient():
    # VRd has no carfilzomib
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[CARF])
    pi = _mm_pi(VRD)
    result = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([VRD], False)
    assert result == 'not_matched'


# ── queryset-level filtering ─────────────────────────────────────────────────

def test_queryset_excludes_trial_when_patient_had_excluded_regimen():
    keep = TrialFactory(disease='multiple myeloma', omop_therapies_excluded=[DARA_MONO])
    also_keep = TrialFactory(disease='multiple myeloma', omop_therapies_excluded=[])
    ids = set(Trial.objects.eligible_for_therapy_from_lines([VRD, KRD]).values_list('id', flat=True))
    assert keep.id in ids        # patient never had daratumumab
    assert also_keep.id in ids


def test_queryset_excludes_trial_when_patient_had_that_exact_regimen():
    excluded = TrialFactory(disease='multiple myeloma', omop_therapies_excluded=[VRD])
    ids = set(Trial.objects.eligible_for_therapy_from_lines([VRD, KRD]).values_list('id', flat=True))
    assert excluded.id not in ids


def test_queryset_triple_line_patient_excluded_by_later_line_regimen():
    # Trial excludes anyone who ever had Dara-VRd
    excluded = TrialFactory(disease='multiple myeloma', omop_therapies_excluded=[DARA_VRD])
    ids = set(Trial.objects.eligible_for_therapy_from_lines([VRD, KRD, DARA_VRD]).values_list('id', flat=True))
    assert excluded.id not in ids


def test_queryset_pi_naive_trial_only_matches_no_prior_therapy():
    naive_only = TrialFactory(
        disease='multiple myeloma',
        omop_therapies_required=[],
        omop_therapy_components_required=[],
        therapy_types_required=[],
    )
    with_req = TrialFactory(
        disease='multiple myeloma',
        omop_therapies_required=[VRD],
    )
    qs = Trial.objects.eligible_for_therapy_related_things_from_lines([], has_no_prior_therapy=True)
    ids = set(qs.values_list('id', flat=True))
    assert naive_only.id in ids
    assert with_req.id not in ids
