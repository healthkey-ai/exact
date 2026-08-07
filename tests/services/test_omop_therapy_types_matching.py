"""OMOP-native drug-class TYPE matching under EXACT_OMOP_THERAPY_TYPES (#285).

promop ADR 0002 reverses "types are not OMOP-mapped": the patient now carries
pre-expanded drug-class concept_ids (PatientInfo.therapy_type_ids,
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
STALE_CLASS = '35800001'    # a class concept_id ABSENT from the mirror (stale)
INVALID_CLASS = '35800002'  # present in the mirror but invalidated (invalid_reason)
_RID = 1                    # the seeded active mirror release_id


OMOP_TYPES = dict(EXACT_OMOP_THERAPY=True, EXACT_OMOP_THERAPY_TYPES=True)


def _mirror_concept(concept_id, invalid_reason=None):
    from vocab_mirror.models import MirrorConcept
    MirrorConcept.objects.create(
        release_id=_RID, concept_id=int(concept_id), concept_name=f'c{concept_id}',
        domain_id='Drug', vocabulary_id='HemOnc', concept_class_id='Component Class',
        concept_code=str(concept_id), invalid_reason=invalid_reason)


@pytest.fixture(autouse=True)
def _reset_type_metrics():
    """Zero the process-local #286 shadow counters before each test, so a metric
    assertion can never inherit accumulated state from an earlier measure=True
    queryset test (the counters are process-global)."""
    from trials.services.omop import type_release_metrics as m
    from trials.services.omop import patient_release_metrics as prm
    m.reset()
    prm.reset()


@pytest.fixture(autouse=True)
def _active_mirror(db):
    """Seed an ACTIVE vocab-mirror release with the test class ids present + valid,
    so #286 per-concept validation admits them (an absent/invalidated id is stale).
    The derive-only tests don't read the mirror; the extra rows are harmless."""
    from vocab_mirror.models import MirrorRelease
    MirrorRelease.objects.create(release_id=_RID, state=MirrorRelease.ACTIVE)
    _mirror_concept(PI_CLASS)
    _mirror_concept(IMID_CLASS)
    _mirror_concept(INVALID_CLASS, invalid_reason='D')


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
                     therapy_type_ids=[int(PI_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    assert _match(trial, pi) == 'matched'


@override_settings(**OMOP_TYPES)
def test_type_required_miss_not_matched():
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(IMID_CLASS)])
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
                     therapy_type_ids=[int(PI_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[PI_CLASS])
    assert _match(trial, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_no_required_type_matches():
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[])
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
                     therapy_component_ids=[int(BORT_CID)], therapy_type_ids=[int(PI_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesRequired']['status'] == 'matched'


@override_settings(**OMOP_TYPES)
def test_display_type_required_miss_not_matched():
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=REG, prior_therapy='One line',
                     therapy_component_ids=[int(BORT_CID)], therapy_type_ids=[int(IMID_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesRequired']['status'] == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_display_type_excluded_hit_not_matched():
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=REG, prior_therapy='One line',
                     therapy_component_ids=[int(BORT_CID)], therapy_type_ids=[int(PI_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[PI_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesExcluded']['status'] == 'not_matched'


# ── #286 Gate 1 (patient-release consistency) — OBSERVE-ONLY (no verdict change) ──
# active mirror release is _RID (the autouse _active_mirror fixture). This slice only
# measures release skew; enforcement (fail-closing on a mismatch) is a later slice.

@override_settings(**OMOP_TYPES)
def test_display_release_mismatch_observe_only_unchanged():
    # A stale patient release must NOT change the verdict/display in this slice.
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=REG, prior_therapy='One line',
                     therapy_component_ids=[int(BORT_CID)], therapy_type_ids=[int(PI_CLASS)],
                     therapy_release_id=str(_RID + 1))
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesRequired']['status'] == 'matched'  # unchanged (observe-only)


@override_settings(**OMOP_TYPES)
def test_queryset_release_mismatch_observe_only_records_skew():
    # A stale-release patient is still matched (observe-only), but the search records
    # one release-skew shadow event.
    from trials.services.omop import patient_release_metrics as prm
    prm.reset()
    needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    pi = PatientInfo(disease='multiple myeloma', prior_therapy='One line',
                     therapy_type_ids=[int(PI_CLASS)], therapy_release_id=str(_RID + 1))
    scope, _ = Trial.objects.filter_by_patient_info(pi)
    assert needs.id in set(scope.values_list('id', flat=True))  # unchanged (observe-only)
    assert prm.skew_search_count() == 1                         # skew observed


@override_settings(**OMOP_TYPES)
def test_queryset_release_match_no_skew():
    from trials.services.omop import patient_release_metrics as prm
    prm.reset()
    TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    pi = PatientInfo(disease='multiple myeloma', prior_therapy='One line',
                     therapy_type_ids=[int(PI_CLASS)], therapy_release_id=str(_RID))
    Trial.objects.filter_by_patient_info(pi)
    assert prm.skew_search_count() == 0


@override_settings(**OMOP_TYPES)
def test_queryset_matcher_parity_on_same_patient_trial():
    # The queryset prefilter and the matcher verdict must agree for the same
    # non-empty patient + trial (no admit-then-reject or drop-a-match asymmetry).
    match = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    miss = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[IMID_CLASS])
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(PI_CLASS)])
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


# ── #286 Gate 2: per-concept release validation (asymmetric) ─────────

@override_settings(**OMOP_TYPES)
def test_type_required_stale_class_dropped_not_matched():
    # An absent (stale) class id is DROPPED → a required-type trial for it is
    # not_matched, even though the patient carries it on the wire.
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(STALE_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[STALE_CLASS])
    assert _match(trial, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_type_required_invalidated_class_dropped_not_matched():
    # Present but invalidated (invalid_reason set) is stale too → dropped.
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(INVALID_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[INVALID_CLASS])
    assert _match(trial, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_type_required_partial_stale_valid_id_still_matches():
    # A stale sibling must NOT sink an otherwise-valid required match.
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(PI_CLASS), int(STALE_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    assert _match(trial, pi) == 'matched'


@override_settings(**OMOP_TYPES)
def test_type_excluded_unvalidated_is_conservative_not_matched():
    # FAIL-OPEN guard: the patient carries a stale id and the trial excludes SOME
    # type — we cannot prove the stale id doesn't hit the exclusion, so not_matched,
    # even though the excluded id differs from the validated one.
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(PI_CLASS), int(STALE_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[IMID_CLASS])
    assert _match(trial, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_no_active_release_fails_closed_required_and_excluded():
    # No active mirror release → validate fails closed: required drops to empty
    # (not_matched) and any excluded constraint is conservatively rejected.
    from vocab_mirror.models import MirrorRelease
    MirrorRelease.objects.filter(state=MirrorRelease.ACTIVE).update(state=MirrorRelease.SUPERSEDED)
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(PI_CLASS)])
    req = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    exc = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[IMID_CLASS])
    assert _match(req, pi) == 'not_matched'
    assert _match(exc, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_queryset_stale_class_parity_with_matcher():
    # Queryset prefilter and matcher verdict agree under a stale patient id.
    needs_stale = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[STALE_CLASS])
    needs_valid = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    excl_other = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[IMID_CLASS])
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(PI_CLASS), int(STALE_CLASS)])
    qs = set(
        Trial.objects.filter(id__in=[needs_stale.id, needs_valid.id, excl_other.id])
        .eligible_for_omop_therapy_types([PI_CLASS, STALE_CLASS]).values_list('id', flat=True)
    )
    assert qs == {needs_valid.id}                    # stale-required dropped; excluded-trial rejected
    assert _match(needs_valid, pi) == 'matched'
    assert _match(needs_stale, pi) == 'not_matched'
    assert _match(excl_other, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_display_type_required_stale_dropped_not_matched():
    # Detail parity with the verdict: a stale required class id must NOT render
    # 'matched' in the criterion breakdown (was the display/verdict divergence).
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=REG, prior_therapy='One line',
                     therapy_component_ids=[int(BORT_CID)], therapy_type_ids=[int(STALE_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[STALE_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesRequired']['status'] == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_display_type_excluded_unvalidated_conservative_not_matched():
    # Detail parity: patient carries a stale id + the trial excludes some type →
    # conservative not_matched (never render an unvalidated exclusion as matched).
    pi = PatientInfo(disease='multiple myeloma', first_line_therapy=REG, prior_therapy='One line',
                     therapy_component_ids=[int(BORT_CID)], therapy_type_ids=[int(PI_CLASS), int(STALE_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[IMID_CLASS])
    out = UserToTrialAttrMatcher(trial, pi).therapy_related_things_match_status()
    assert out['therapyTypesExcluded']['status'] == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_queryset_no_active_release_fails_closed():
    # Queryset-side parity with the matcher no-release case: no active mirror →
    # required dropped + excluded-type trials rejected → only the no-constraint trial.
    from vocab_mirror.models import MirrorRelease
    MirrorRelease.objects.filter(state=MirrorRelease.ACTIVE).update(state=MirrorRelease.SUPERSEDED)
    needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    open_ = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[])
    excl = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[IMID_CLASS])
    qs = set(
        Trial.objects.filter(id__in=[needs.id, open_.id, excl.id])
        .eligible_for_omop_therapy_types([PI_CLASS]).values_list('id', flat=True)
    )
    assert qs == {open_.id}


@override_settings(**OMOP_TYPES)
def test_shadow_metric_records_stale_ids_once_per_search():
    # The queryset records the #286 shadow signal ONCE per search — not per trial —
    # so a multi-trial candidate set still records a single stale-search event.
    from trials.services.omop import type_release_metrics as m
    a = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    b = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[IMID_CLASS])
    Trial.objects.filter(id__in=[a.id, b.id]).eligible_for_omop_therapy_types([PI_CLASS, STALE_CLASS])
    assert m.stale_search_count() == 1         # once per search, not once per trial
    assert m.dropped_ids_total() == 1          # STALE_CLASS dropped; PI_CLASS valid


@override_settings(**OMOP_TYPES)
def test_shadow_metric_quiet_when_all_valid():
    from trials.services.omop import type_release_metrics as m
    m.reset()
    needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    Trial.objects.filter(id=needs.id).eligible_for_omop_therapy_types([PI_CLASS])
    assert m.stale_search_count() == 0         # no stale id → no log/record
    assert m.dropped_ids_total() == 0


@override_settings(**OMOP_TYPES)
def test_shadow_metric_not_recorded_per_trial_in_matcher():
    # The per-trial matcher leaves measure off → no per-trial flooding.
    from trials.services.omop import type_release_metrics as m
    m.reset()
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[int(PI_CLASS), int(STALE_CLASS)])
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    _match(trial, pi)
    assert m.stale_search_count() == 0


@override_settings(**OMOP_TYPES)
def test_memo_dedups_validation_to_one_query(django_assert_num_queries):
    # The per-request memo collapses repeated per-trial validation of the same
    # class-id set to a single mirror query (order-independent key).
    from vocab_mirror.release_context import MatchingReleaseContext
    from trials.services.omop.type_release_gate import (
        resolve_type_validation, type_validation_request_cache)
    with MatchingReleaseContext(), type_validation_request_cache():
        with django_assert_num_queries(1):
            v1, u1 = resolve_type_validation([PI_CLASS, IMID_CLASS])
            v2, u2 = resolve_type_validation([IMID_CLASS, PI_CLASS])  # same set, different order
    assert v1 == v2 == {PI_CLASS, IMID_CLASS}
    assert u1 is False and u2 is False
