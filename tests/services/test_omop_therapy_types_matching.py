"""OMOP-native drug-class TYPE matching under EXACT_OMOP_THERAPY_TYPES (#285).

promop ADR 0002 reverses "types are not OMOP-mapped": the patient now carries
pre-expanded drug-class concept_ids (PatientInfo.therapy_component_class_ids,
promop#370). With EXACT_OMOP_THERAPY + EXACT_OMOP_THERAPY_TYPES on:
- the profile flips therapy_types_* -> omop_therapy_types_* (class concept_id columns);
- derive returns the consumer's class concept_ids as type_values (no category lookup);
- matching is class-concept_id overlap, FAIL-CLOSED on unknown/empty patient classes.
"""
import pytest
from django.test import override_settings

from trials.models import Trial
from trials.services.omop.therapy_graph import derive_component_and_type_values
from trials.services.therapy_match_profile import (
    get_therapy_match_profile, OMOP_THERAPY_WITH_TYPES_MATCH_PROFILE,
    OMOP_THERAPY_MATCH_PROFILE, LEGACY_THERAPY_MATCH_PROFILE,
)
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.patient_info.patient_info import PatientInfo
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

# HemOnc class concept_ids (as strings on the wire) + a component concept_id.
PI_CLASS = '35807295'    # proteasome inhibitor
IMID_CLASS = '35807403'  # IMiD
BORT_CID = '900825'      # a component concept_id (only needs to be non-empty)
REG = '900260'           # a regimen concept_id


OMOP_TYPES = dict(EXACT_OMOP_THERAPY=True, EXACT_OMOP_THERAPY_TYPES=True)


# ── profile selection ────────────────────────────────────────────────

def test_profile_selection_matrix():
    with override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_THERAPY_TYPES=False):
        assert get_therapy_match_profile() is LEGACY_THERAPY_MATCH_PROFILE
    with override_settings(EXACT_OMOP_THERAPY=True, EXACT_OMOP_THERAPY_TYPES=False):
        assert get_therapy_match_profile() is OMOP_THERAPY_MATCH_PROFILE
    with override_settings(**OMOP_TYPES):
        p = get_therapy_match_profile()
        assert p is OMOP_THERAPY_WITH_TYPES_MATCH_PROFILE
        assert p.therapy_types_required == 'omop_therapy_types_required'
    # types flag ignored when OMOP therapy is off (types ride the OMOP profile)
    with override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_THERAPY_TYPES=True):
        assert get_therapy_match_profile() is LEGACY_THERAPY_MATCH_PROFILE


# ── derive: types = the patient's class concept_ids ──────────────────

@override_settings(**OMOP_TYPES)
def test_derive_returns_patient_class_ids_as_types():
    comps, types = derive_component_and_type_values([REG], [BORT_CID], patient_class_ids=[PI_CLASS])
    assert comps == [BORT_CID]
    assert types == [PI_CLASS]          # class concept_ids as-is, no category lookup


def test_types_flag_ignored_without_omop_therapy():
    # codex [P2]: the types flag alone must NOT divert onto the OMOP-types path
    # (queryset/matcher) while the profile still names legacy columns.
    from trials.services.therapy_match_profile import omop_therapy_types_enabled
    with override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_THERAPY_TYPES=True):
        assert omop_therapy_types_enabled() is False


@override_settings(**OMOP_TYPES)
def test_derive_class_only_patient_still_derives_types():
    # codex [P2]: class ids but no component ids (the #224 fold routes such patients
    # here) — types must still derive from the class ids, not false-negative.
    comps, types = derive_component_and_type_values([REG], None, patient_class_ids=[PI_CLASS])
    assert comps is None                # components unknown
    assert types == [PI_CLASS]          # types derived from class ids


@override_settings(**OMOP_TYPES)
def test_display_unknown_classes_fail_closed_even_with_blank_line():
    # codex [P2]: missing class ids + required type -> detail status not_matched,
    # agreeing with the verdict, even when the therapy-line mismatch is 'unknown'
    # (no first_line_therapy here -> mismatch_status would be 'unknown').
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesRequired']['status'] == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_derive_none_class_ids_is_unknown_types():
    _, types = derive_component_and_type_values([REG], [BORT_CID], patient_class_ids=None)
    assert types is None                # unknown -> fail-closed downstream


@override_settings(**OMOP_TYPES)
def test_derive_empty_class_ids_is_known_empty_types():
    _, types = derive_component_and_type_values([REG], [BORT_CID], patient_class_ids=[])
    assert types == []


@override_settings(EXACT_OMOP_THERAPY=True, EXACT_OMOP_THERAPY_TYPES=False)
def test_derive_ignores_class_ids_when_types_flag_off():
    # types flag off: class ids are ignored; legacy category-code path is used.
    _, types = derive_component_and_type_values([REG], [BORT_CID], patient_class_ids=[PI_CLASS])
    assert types == []                  # no category lookup rows -> [] (not the class ids)


# ── matcher verdict (fail-closed) ────────────────────────────────────

def _match(trial, pi):
    return UserToTrialAttrMatcher(trial, pi)._match_therapy_related_things([REG], False)


@override_settings(**OMOP_TYPES)
def test_type_required_overlap_matches():
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_component_class_ids=[int(PI_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    assert _match(trial, pi) == 'matched'


@override_settings(**OMOP_TYPES)
def test_type_required_miss_not_matched():
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_component_class_ids=[int(IMID_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    assert _match(trial, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_type_required_unknown_patient_classes_fail_closed():
    # Consumer sent no class ids (field absent) -> unknown -> a required type is a
    # hard not_matched, NOT 'unknown'/matched.
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    assert _match(trial, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_type_excluded_hit_not_matched():
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_component_class_ids=[int(PI_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[PI_CLASS])
    assert _match(trial, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_no_required_type_matches():
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_component_class_ids=[])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[])
    assert _match(trial, pi) == 'matched'


# ── queryset fail-closed (parity with the matcher verdict) ───────────

@override_settings(**OMOP_TYPES)
def test_queryset_unknown_classes_keeps_only_no_required_type_trials():
    needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    open_ = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[])
    # None (unknown) patient classes -> fail-closed: only the no-required-type trial.
    qs = Trial.objects.filter(id__in=[needs.id, open_.id]).eligible_for_omop_therapy_types(None)
    ids = set(qs.values_list('id', flat=True))
    assert ids == {open_.id}


# ── detail display status (parity with the verdict) ──────────────────

@override_settings(**OMOP_TYPES)
def test_display_type_required_matched_uses_class_ids():
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=REG, prior_therapy='One line',
                     therapy_component_ids=[int(BORT_CID)], therapy_component_class_ids=[int(PI_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesRequired']['status'] == 'matched'


@override_settings(**OMOP_TYPES)
def test_display_type_required_miss_not_matched():
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=REG, prior_therapy='One line',
                     therapy_component_ids=[int(BORT_CID)], therapy_component_class_ids=[int(IMID_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesRequired']['status'] == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_display_type_excluded_hit_not_matched():
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=REG, prior_therapy='One line',
                     therapy_component_ids=[int(BORT_CID)], therapy_component_class_ids=[int(PI_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[PI_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesExcluded']['status'] == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_queryset_matcher_parity_on_same_patient_trial():
    # The queryset prefilter and the matcher verdict must agree for the same
    # non-empty patient + trial (no admit-then-reject or drop-a-match asymmetry).
    match = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    miss = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[IMID_CLASS])
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_component_class_ids=[int(PI_CLASS)])
    qs_ids = set(
        Trial.objects.filter(id__in=[match.id, miss.id])
        .eligible_for_omop_therapy_types([PI_CLASS]).values_list('id', flat=True)
    )
    assert qs_ids == {match.id}                                   # queryset admits match, drops miss
    assert _match(match, pi) == 'matched'                         # matcher agrees: match
    assert _match(miss, pi) == 'not_matched'                      # matcher agrees: miss


@override_settings(**OMOP_TYPES)
def test_non_empty_patient_still_sees_no_required_type_trial():
    # A patient WITH classes must still see a trial that requires no types.
    open_ = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[])
    qs = Trial.objects.filter(id=open_.id).eligible_for_omop_therapy_types([PI_CLASS])
    assert set(qs.values_list('id', flat=True)) == {open_.id}


@override_settings(**OMOP_TYPES)
def test_queryset_overlap_and_exclusion():
    match = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    other = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[IMID_CLASS])
    excl = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[PI_CLASS])
    qs = Trial.objects.filter(id__in=[match.id, other.id, excl.id]).eligible_for_omop_therapy_types([PI_CLASS])
    ids = set(qs.values_list('id', flat=True))
    assert match.id in ids           # required overlap
    assert other.id not in ids       # required miss
    assert excl.id not in ids        # excluded hit rejects
