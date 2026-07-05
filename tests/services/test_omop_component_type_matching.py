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
    TherapyComponentConnection, TherapyComponentCategoryConnection, OmopConcept,
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

@override_settings(EXACT_OMOP_THERAPY=False)
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


@override_settings(EXACT_OMOP_THERAPY=True)
def test_derive_none_for_components_when_all_component_concept_ids_null():
    """B1 regression: regimen found but all its components have omop_concept_id=NULL.
    Must return component_values=None (unknown) not [] (no components), so
    _match_therapy_things treats it as unknown rather than not_matched."""
    vrd = Therapy.objects.create(code='zz_vrd_b1', title='zz VRd B1', omop_concept_id=900991)
    comp = TherapyComponent.objects.create(code='zz_bort_b1', title='zz Bort B1', omop_concept_id=None)
    TherapyComponentConnection.objects.create(therapy=vrd, component=comp)

    component_values, _ = derive_component_and_type_values(['900991'])
    assert component_values is None


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

# ── detail display (therapy_related_things_match_status) under OMOP ───

@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_component_required_status_via_concept_id():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID), prior_therapy='One line')
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[str(BORT_CID)])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyComponentsRequired']['status'] == 'matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_component_excluded_status_via_concept_id():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID), prior_therapy='One line')
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_excluded=[str(BORT_CID)])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyComponentsExcluded']['status'] == 'not_matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_type_required_status_via_cb_graph():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID), prior_therapy='One line')
    trial = TrialFactory(disease='multiple myeloma', therapy_types_required=['zz_proteasome_inh'])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesRequired']['status'] == 'matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_type_excluded_status_via_cb_graph():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID), prior_therapy='One line')
    trial = TrialFactory(disease='multiple myeloma', therapy_types_excluded=['zz_proteasome_inh'])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesExcluded']['status'] == 'not_matched'


# ── detail response: OMOP code + title (omopConcepts) ────────────────

@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_includes_omop_concepts_code_and_title():
    _graph()
    OmopConcept.objects.create(concept_id=BORT_CID, concept_name='bortezomib', vocabulary_id='RxNorm')
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID), prior_therapy='One line')
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[str(BORT_CID)])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyComponentsRequired']['omopConcepts'] == [
        {'code': BORT_CID, 'title': 'bortezomib', 'vocab': 'RxNorm'}
    ]
    # types are CB-coded (not OMOP) → no omopConcepts on the type criterion
    assert 'omopConcepts' not in out['therapyTypesRequired']


@override_settings(EXACT_OMOP_THERAPY=True)
def test_omop_concepts_unresolved_keeps_code():
    _graph()  # no OmopConcept row for BORT_CID
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID), prior_therapy='One line')
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[str(BORT_CID)])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyComponentsRequired']['omopConcepts'] == [
        {'code': BORT_CID, 'title': None, 'vocab': None}
    ]


@override_settings(EXACT_OMOP_THERAPY=False)
def test_no_omop_concepts_field_when_flag_off():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy='zz_vrd', prior_therapy='One line')
    trial = TrialFactory(disease='multiple myeloma', therapy_components_required=['zz_bort'])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert 'omopConcepts' not in out['therapyComponentsRequired']


@override_settings(EXACT_OMOP_THERAPY=False)
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
