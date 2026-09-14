"""OMOP component matching under Phase P (#234).

Under EXACT_OMOP_THERAPY:
- regimen matches on OMOP concept_ids (omop_therapies_* columns);
- component match-values are the consumer-supplied PRE-EXPANDED concept_ids
  (PatientInfo.therapy_component_ids), NOT reverse-mapped from the regimen via the
  local CB graph.

Drug-class TYPE matching is now folded into the base flag (#285) and matches the
patient's pre-expanded class concept_ids against omop_therapy_types_* — covered by
tests/services/test_omop_therapy_types_matching.py. The old mode-2 type path
(component concept_ids -> ComponentCategoryOmopLookup CB category codes -> legacy
therapy_types_* columns) is retired under OMOP; the type tests that exercised it were
removed. Legacy (flag OFF) type matching via the CB M2M graph is unchanged.
"""
import pytest
from django.test import override_settings

from trials.models import (
    Therapy, TherapyComponent, TherapyComponentCategory,
    TherapyComponentConnection, TherapyComponentCategoryConnection,
)
from trials.services.omop.therapy_graph import derive_component_and_type_values
from trials.services.omop.component_category_lookup import sync_component_category_lookup
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
    # (pi_cat + the M2M connection are used only by the legacy — flag OFF — type path,
    # which reads the CB M2M graph directly. The flat ComponentCategoryOmopLookup is no
    # longer consulted for types under OMOP, #285.)
    return vrd, bort, lena, pi_cat


# ── shared derivation helper ─────────────────────────────────────────

@override_settings(EXACT_OMOP_THERAPY=False)
def test_derive_legacy_codes_when_flag_off():
    _graph()
    comps, types = derive_component_and_type_values(['zz_vrd'])
    assert sorted(comps) == ['zz_bort', 'zz_lena']           # internal codes
    assert types == ['zz_proteasome_inh']                    # CB category code


@override_settings(EXACT_OMOP_THERAPY=True)
def test_derive_uses_consumer_supplied_component_ids_when_flag_on():
    _graph()
    # Phase P: components come from the pre-expanded ids, not the regimen.
    comps, types = derive_component_and_type_values([str(VRD_CID)], [str(BORT_CID), str(LEN_CID)])
    assert sorted(comps) == [str(BORT_CID), str(LEN_CID)]    # consumer-supplied concept_ids
    # types folded into the base flag (#285): with no class ids supplied -> unknown (None).
    assert types is None


@override_settings(EXACT_OMOP_THERAPY=True)
def test_derive_none_component_ids_is_unknown():
    _graph()
    # Consumer sent no therapy_component_ids → unknown (None), not known-empty.
    assert derive_component_and_type_values([str(VRD_CID)], None) == (None, None)


@override_settings(EXACT_OMOP_THERAPY=True)
def test_derive_empty_component_ids_is_known_empty():
    _graph()
    # components known-empty; types unknown (no class ids supplied -> None, #285)
    assert derive_component_and_type_values([str(VRD_CID)], []) == ([], None)


@override_settings(EXACT_OMOP_THERAPY=True)
def test_derive_ignores_regimen_under_omop():
    _graph()
    # The regimen list is irrelevant to components under Phase P — only the
    # supplied component ids drive the result.
    comps, _ = derive_component_and_type_values(['99999999'], [str(BORT_CID)])
    assert comps == [str(BORT_CID)]


# ── matcher: component level on consumer-supplied concept_ids ─────────

@override_settings(EXACT_OMOP_THERAPY=True)
def test_component_required_matches_via_concept_id():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[BORT_CID, LEN_CID])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[str(BORT_CID)])
    res = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([str(VRD_CID)], False)
    assert res == 'matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_component_excluded_blocks_via_concept_id():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[BORT_CID, LEN_CID])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_excluded=[str(BORT_CID)])
    res = UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([str(VRD_CID)], False)
    assert res == 'not_matched'


# (Mode-2 type matching via the component→category lookup is retired under OMOP, #285.
# Mode-3 type matching — patient class ids overlapped against omop_therapy_types_* — is
# covered by tests/services/test_omop_therapy_types_matching.py.)


# ── detail display (therapy_related_things_match_status) under OMOP ───

@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_component_required_status_via_concept_id():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID),
                     prior_therapy='One line', therapy_component_ids=[BORT_CID])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[str(BORT_CID)])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyComponentsRequired']['status'] == 'matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_component_excluded_status_via_concept_id():
    _graph()
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID),
                     prior_therapy='One line', therapy_component_ids=[BORT_CID])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_excluded=[str(BORT_CID)])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyComponentsExcluded']['status'] == 'not_matched'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_component_title_falls_back_to_concept_id_when_not_local():
    """Primary Phase P case: promop supplies a component concept_id EXACT holds no
    local TherapyComponent row for. The display must fall back to the concept_id
    string (a None title would TypeError in match_required's sorted(set(values)))."""
    _graph()  # local rows exist for BORT_CID / LEN_CID only
    unmapped = 999123456
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID),
                     prior_therapy='One line', therapy_component_ids=[BORT_CID, unmapped])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[str(BORT_CID)])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()  # must not crash
    assert out['therapyComponentsRequired']['status'] == 'matched'
    assert str(unmapped) in out['therapyComponentsRequired']['values']  # shown by concept_id


# ── detail response: OMOP code + title (omopConcepts) ────────────────

def _activate_mirror(release_id, concepts):
    """Seed a minimal complete generation (concepts + one gate row per other
    table) and activate it, so title resolution reads it (#252)."""
    from vocab_mirror.activation import activate_release
    from vocab_mirror.models import (
        MirrorConcept, MirrorConceptAncestor, MirrorConceptRelationship,
        MirrorRelease, MirrorVocabulary,
    )
    MirrorVocabulary.objects.create(release_id=release_id, vocabulary_id='V',
                                    vocabulary_name='V', vocabulary_concept_id=1)
    MirrorConceptRelationship.objects.create(release_id=release_id, concept_id_1=1,
                                             concept_id_2=2, relationship_id='seed')
    MirrorConceptAncestor.objects.create(release_id=release_id, ancestor_concept_id=1,
                                         descendant_concept_id=2,
                                         min_levels_of_separation=1, max_levels_of_separation=1)
    for cid, name, vocab in concepts:
        MirrorConcept.objects.create(release_id=release_id, concept_id=cid, concept_name=name,
                                     domain_id='Drug', vocabulary_id=vocab,
                                     concept_class_id='Ingredient', concept_code=str(cid))
    MirrorRelease.objects.create(release_id=release_id, state=MirrorRelease.READY)
    # Production activates via the sync flow, which rebuilds + stamps the component
    # lookup for the release before activating (#262), so the cross-artifact gate is
    # satisfied. Mirror that here.
    sync_component_category_lookup(release_id=release_id)
    activate_release(release_id)


@override_settings(EXACT_OMOP_THERAPY=True)
def test_display_includes_omop_concepts_code_and_title():
    _graph()
    _activate_mirror(1, [(BORT_CID, 'bortezomib', 'RxNorm')])  # titles come from the mirror now
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID),
                     prior_therapy='One line', therapy_component_ids=[BORT_CID])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_components_required=[str(BORT_CID)])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyComponentsRequired']['omopConcepts'] == [
        {'code': BORT_CID, 'title': 'bortezomib', 'vocab': 'RxNorm'}
    ]


@override_settings(EXACT_OMOP_THERAPY=True)
def test_omop_concepts_unresolved_keeps_code():
    _graph()  # no OmopConcept row for BORT_CID
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=str(VRD_CID),
                     prior_therapy='One line', therapy_component_ids=[BORT_CID])
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


# ── legacy unchanged ─────────────────────────────────────────────────

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
