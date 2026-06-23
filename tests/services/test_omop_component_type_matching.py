"""OMOP component + type matching via the CB graph (#197 design).

Under EXACT_OMOP_THERAPY:
- regimen + component match on OMOP concept_ids (omop_* columns);
- type/category is NOT OMOP-mapped — matched through the CB graph
  (categories ↔ components ↔ therapies) against the LEGACY therapy_types_* columns.

The matcher reverse-maps the patient's regimen concept_ids to internal Therapies
(Therapy.omop_concept_id), walks to components (→ their OMOP concept_ids) and to
categories (→ CB codes).
"""
import pytest
from django.test import override_settings

from trials.models import (
    Therapy, TherapyComponent, TherapyComponentCategory,
    TherapyComponentConnection, TherapyComponentCategoryConnection,
)
from trials.services.omop.therapy_graph import derive_component_and_type_values
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.patient_info.patient_info import PatientInfo
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

# concept_ids for the test graph (zz_ codes avoid seeded-data collisions)
VRD_CID, BORT_CID, LEN_CID = 900260, 900825, 900972


def _graph():
    vrd = Therapy.objects.create(code='zz_vrd', title='zz VRd', omop_concept_id=VRD_CID)
    bort = TherapyComponent.objects.create(code='zz_bort', title='zz Bortezomib', omop_concept_id=BORT_CID)
    lena = TherapyComponent.objects.create(code='zz_lena', title='zz Lenalidomide', omop_concept_id=LEN_CID)
    pi_cat = TherapyComponentCategory.objects.create(code='zz_proteasome_inh', title='zz Proteasome Inhibitor')
    TherapyComponentConnection.objects.create(therapy=vrd, component=bort)
    TherapyComponentConnection.objects.create(therapy=vrd, component=lena)
    TherapyComponentCategoryConnection.objects.create(category=pi_cat, component=bort)
    return vrd, bort, lena, pi_cat


# ── shared derivation helper ─────────────────────────────────────────

def test_derive_legacy_codes_when_flag_off():
    _graph()
    comps, types = derive_component_and_type_values(['zz_vrd'])
    assert sorted(comps) == ['zz_bort', 'zz_lena']           # internal codes
    assert types == ['zz_proteasome_inh']                    # CB category code


@override_settings(EXACT_OMOP_THERAPY=True)
def test_derive_concept_ids_for_components_cb_codes_for_types_when_flag_on():
    _graph()
    comps, types = derive_component_and_type_values([str(VRD_CID)])  # patient sends regimen concept_id
    assert sorted(comps) == [str(BORT_CID), str(LEN_CID)]    # component OMOP concept_ids
    assert types == ['zz_proteasome_inh']                    # types stay CB codes (not OMOP-mapped)


@override_settings(EXACT_OMOP_THERAPY=True)
def test_derive_none_when_regimen_concept_unknown():
    _graph()
    assert derive_component_and_type_values(['99999999']) == (None, None)


# ── matcher: component level on OMOP concepts ────────────────────────

@override_settings(EXACT_OMOP_THERAPY=True)
def test_component_required_matches_via_concept_id():
    _graph()
    pi = PatientInfo(disease='multiple myeloma')
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[str(BORT_CID)])
    res = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([str(VRD_CID)], False)
    assert res == 'matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_component_excluded_blocks_via_concept_id():
    _graph()
    pi = PatientInfo(disease='multiple myeloma')
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_excluded=[str(BORT_CID)])
    res = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([str(VRD_CID)], False)
    assert res == 'not_matched'


# ── matcher: type level via CB category graph (legacy column) ─────────

@override_settings(EXACT_OMOP_THERAPY=True)
def test_type_required_matches_via_cb_category_graph():
    _graph()
    pi = PatientInfo(disease='multiple myeloma')
    # type column stays LEGACY (CB code) even under OMOP
    trial = TrialFactory(disease='multiple myeloma', therapy_types_required=['zz_proteasome_inh'])
    res = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([str(VRD_CID)], False)
    assert res == 'matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_type_excluded_blocks_via_cb_category_graph():
    _graph()
    pi = PatientInfo(disease='multiple myeloma')
    trial = TrialFactory(disease='multiple myeloma', therapy_types_excluded=['zz_proteasome_inh'])
    res = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([str(VRD_CID)], False)
    assert res == 'not_matched'


# ── legacy unchanged ─────────────────────────────────────────────────

def test_legacy_component_and_type_still_match_by_code():
    _graph()
    pi = PatientInfo(disease='multiple myeloma')
    trial = TrialFactory(
        disease='multiple myeloma',
        therapy_components_required=['zz_bort'],
        therapy_types_required=['zz_proteasome_inh'],
    )
    res = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things(['zz_vrd'], False)
    assert res == 'matched'
