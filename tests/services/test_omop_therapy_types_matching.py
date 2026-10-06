"""OMOP-native drug-class TYPE matching — folded into the base EXACT_OMOP_THERAPY flag (#285).

promop ADR 0002 reverses "types are not OMOP-mapped": the patient now carries
pre-expanded drug-class concept_ids (PatientInfo.therapy_type_ids, promop#370). Types
are part of the one OMOP therapy projection (no separate flag). With EXACT_OMOP_THERAPY on:
- the profile flips therapy_types_* -> omop_therapy_types_* (class concept_id columns);
- derive returns the consumer's class concept_ids as type_values (no category lookup);
- matching is class-concept_id overlap, FAIL-CLOSED on unknown/empty patient classes.
"""
from contextlib import contextmanager

import pytest
from django.test import override_settings

from trials.models import Trial
from trials.services.omop.therapy_graph import derive_component_and_type_values
from trials.services.therapy_match_profile import (
    get_therapy_match_profile,
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


# Types are folded into the base flag (#285) — no separate EXACT_OMOP_THERAPY_TYPES.
OMOP_TYPES = dict(EXACT_OMOP_THERAPY=True)


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
    # Types are folded into the base flag: base off -> legacy; base on -> the one OMOP
    # profile, which now names the omop_therapy_types_* columns.
    with override_settings(EXACT_OMOP_THERAPY=False):
        assert get_therapy_match_profile() is LEGACY_THERAPY_MATCH_PROFILE
    with override_settings(EXACT_OMOP_THERAPY=True):
        p = get_therapy_match_profile()
        assert p is OMOP_THERAPY_MATCH_PROFILE
        assert p.therapy_types_required == 'omop_therapy_types_required'
        assert p.therapy_types_excluded == 'omop_therapy_types_excluded'


# ── derive: types = the patient's class concept_ids ──────────────────

@override_settings(**OMOP_TYPES)
def test_derive_returns_patient_class_ids_as_types():
    comps, types = derive_component_and_type_values([REG], [BORT_CID], patient_class_ids=[PI_CLASS])
    assert comps == [BORT_CID]
    assert types == [PI_CLASS]          # class concept_ids as-is, no category lookup


def test_types_enabled_tracks_base_flag():
    # Types are folded into the base flag — the type path engages exactly when OMOP
    # therapy is on (no separate toggle).
    from trials.services.therapy_match_profile import (
        omop_therapy_types_enabled, omop_therapy_enabled)
    with override_settings(EXACT_OMOP_THERAPY=False):
        assert omop_therapy_types_enabled() is False
        assert omop_therapy_types_enabled() == omop_therapy_enabled()
    with override_settings(EXACT_OMOP_THERAPY=True):
        assert omop_therapy_types_enabled() is True
        assert omop_therapy_types_enabled() == omop_therapy_enabled()


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


# ── exclusion + empty/unknown patient classes (documented flip-gate policy) ──
# Required types are fail-CLOSED on unknown/empty patient classes (see the
# test_type_required_* cases above). Excluded types are NOT: an empty (known-empty)
# class set does not reject an excluded-type trial. This is INTENTIONAL and preserved
# by the fold (#285) — it is a documented flip-gate decision, guarded by an ingestion
# invariant: a therapy-bearing patient must never emit an EMPTY class list merely
# because pre-expansion failed (that would silently weaken an exclusion). These tests
# pin the current behavior so a future change is a conscious one.

@override_settings(**OMOP_TYPES)
def test_type_excluded_empty_classes_does_not_reject():
    pi = PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                     therapy_type_ids=[])  # known-empty
    trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[PI_CLASS])
    assert _match(trial, pi) == 'matched'  # empty class set does NOT trip the exclusion


@override_settings(**OMOP_TYPES)
def test_queryset_empty_classes_keeps_excluded_type_trial():
    excl = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[PI_CLASS])
    # [] (known-empty) patient classes: the excluded-type trial is NOT dropped.
    qs = Trial.objects.filter(id=excl.id).eligible_for_omop_therapy_types([])
    assert set(qs.values_list('id', flat=True)) == {excl.id}


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


# ── #286 Gate 1 ENFORCED (toggle on) — fail-closed at every seam, parity ────────
# The patient carries its release on therapy_release_id; the matcher verdict/detail
# seams read it via the attr (explicit), the queryset reads it from the scope
# filter_by_patient_info binds. active mirror release = _RID. `+1` = a stale release.

@contextmanager
def _enforce_gate1(monkeypatch):
    from trials.services.omop import patient_release_gate as prg
    monkeypatch.setattr(prg, '_PATIENT_RELEASE_GATE_ENFORCE', True)
    yield


def _pi(release, **kw):
    return PatientInfo(disease='multiple myeloma', therapy_component_ids=[int(BORT_CID)],
                       therapy_type_ids=[int(PI_CLASS)], therapy_release_id=release, **kw)


@override_settings(**OMOP_TYPES)
def test_enforce_verdict_required_stale_not_matched(monkeypatch):
    with _enforce_gate1(monkeypatch):
        trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
        assert _match(trial, _pi(str(_RID + 1))) == 'not_matched'  # raw overlaps, release stale


@override_settings(**OMOP_TYPES)
def test_enforce_verdict_excluded_stale_not_matched(monkeypatch):
    with _enforce_gate1(monkeypatch):
        trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_excluded=[PI_CLASS])
        assert _match(trial, _pi(str(_RID + 1))) == 'not_matched'  # conservative excluded


@override_settings(**OMOP_TYPES)
def test_enforce_consistent_release_matches(monkeypatch):
    # Patient release == active mirror release → Gate 1 passes; Gate 2 alone governs.
    with _enforce_gate1(monkeypatch):
        trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
        assert _match(trial, _pi(str(_RID))) == 'matched'


@override_settings(**OMOP_TYPES)
def test_enforce_display_stale_not_matched(monkeypatch):
    with _enforce_gate1(monkeypatch):
        trial = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
        out = UserToTrialAttrMatcher(trial, _pi(str(_RID + 1))).therapy_related_things_match_status()
        assert out['therapyTypesRequired']['status'] == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_enforce_queryset_stale_drops_required_type(monkeypatch):
    # The queryset reads the release from the scope filter_by_patient_info binds.
    with _enforce_gate1(monkeypatch):
        needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
        open_ = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[])
        pi = _pi(str(_RID + 1), prior_therapy='One line')
        scope, _ = (Trial.objects.filter(id__in=[needs.id, open_.id])
                    .filter_by_patient_info(pi))
        assert set(scope.values_list('id', flat=True)) == {open_.id}  # required dropped


@override_settings(**OMOP_TYPES)
def test_enforce_queryset_matcher_parity_stale(monkeypatch):
    # The prefilter and the verdict must AGREE under enforcement (no admit-then-reject).
    with _enforce_gate1(monkeypatch):
        match = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
        pi = _pi(str(_RID + 1), prior_therapy='One line')
        scope, _ = Trial.objects.filter(id=match.id).filter_by_patient_info(pi)
        assert set(scope.values_list('id', flat=True)) == set()  # queryset drops it
        assert _match(match, pi) == 'not_matched'                 # matcher agrees


@override_settings(**OMOP_TYPES)
def test_enforce_component_only_stale_fail_closed(monkeypatch):
    # Component-less stale patient (only type_ids): the compose approach keeps the raw
    # class ids flowing, so the eligible_for_therapy_related_things_from_lines short-
    # circuit is NOT hit and the type prefilter fails closed (the step-1 bug that the
    # class-id-gating approach caused). Parity with the matcher.
    with _enforce_gate1(monkeypatch):
        needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
        pi = PatientInfo(disease='multiple myeloma', prior_therapy='One line',
                         therapy_type_ids=[int(PI_CLASS)], therapy_release_id=str(_RID + 1))
        scope, _ = Trial.objects.filter_by_patient_info(pi)
        assert needs.id not in set(scope.values_list('id', flat=True))
        assert _match(needs, pi) == 'not_matched'


@override_settings(**OMOP_TYPES)
def test_enforce_empty_class_set_is_noop(monkeypatch):
    # Empty patient class set → resolve_type_validation's empty guard returns BEFORE
    # Gate 1, so enforcement is a no-op; a no-required-type trial stays visible.
    with _enforce_gate1(monkeypatch):
        open_ = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[])
        pi = PatientInfo(disease='multiple myeloma', prior_therapy='One line',
                         therapy_type_ids=[], therapy_release_id=str(_RID + 1))
        scope, _ = Trial.objects.filter(id=open_.id).filter_by_patient_info(pi)
        assert set(scope.values_list('id', flat=True)) == {open_.id}


@override_settings(**OMOP_TYPES)
def test_enforce_embedded_backend_search_stale_drops_required_type(monkeypatch):
    # The EMBEDDED ExactMatcher backend (CancerBot runs it in-process) must enforce Gate 1
    # too. Its search() funnels through filter_by_patient_info (for a patient-scoped
    # search_type — 'all' is the admin/browse path that skips the patient prefilter), so
    # the scope covers it. Explicitly guards the regression where a view-only set-point
    # fail-opened for this path.
    from exact_matching.backend import ExactMatcher
    from trials.services.study_preferences import StudyPreferences
    with _enforce_gate1(monkeypatch):
        needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
        open_ = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[])
        pi = _pi(str(_RID + 1), prior_therapy='One line')  # stale release

        class _P:
            geo_point = None

            def as_patient_info(self):
                return pi

        class _Prefs:
            query_params = {}
            study_info = StudyPreferences()
            search_type = 'standard'  # patient-scoped path → filter_by_patient_info
            recruitment_status = None

        qs = ExactMatcher().search(
            Trial.objects.filter(id__in=[needs.id, open_.id]), _P(), _Prefs())
        ids = set(qs.values_list('id', flat=True))
        assert needs.id not in ids   # embedded backend enforces → stale required-type dropped
        assert open_.id in ids


@override_settings(**OMOP_TYPES)
def test_enforce_embedded_backend_verdict_stale_not_matched(monkeypatch):
    # For 'all' (admin/browse) searches the queryset skips the patient prefilter, so the
    # embedded backend enforces per-trial via the verdict (match_status → matching_type),
    # which reads the release explicitly. A stale-release patient → not_matched.
    from exact_matching.backend import ExactMatcher
    with _enforce_gate1(monkeypatch):
        needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
        pi = _pi(str(_RID + 1))  # stale release

        class _P:
            def as_patient_info(self):
                return pi

        # matching_type's vocabulary is 'not_eligible' (vs the internal matcher's
        # 'not_matched') — Gate 1 fired and the trial is not eligible for the stale patient.
        assert ExactMatcher().match_status(needs, _P()) == 'not_eligible'


def _authed_client():
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient
    from accounts.models import Identity
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='gate1-view')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


def _match_post(client, therapy_release_id):
    return client.post('/trials/match/', {'patient_info': {
        'disease': 'multiple myeloma',
        'therapy_component_ids': [int(BORT_CID)],
        'therapy_type_ids': [int(PI_CLASS)],
        'therapy_release_id': therapy_release_id,
    }}, format='json')


@override_settings(**OMOP_TYPES)
def test_enforce_view_end_to_end_stale_drops_required_type(monkeypatch):
    # Full HTTP chain: the view's _resolve_patient_info publishes the patient release
    # into the request contextvar, so enforcement fail-closes with NO manual set.
    from trials.services.omop import patient_release_gate as prg
    monkeypatch.setattr(prg, '_PATIENT_RELEASE_GATE_ENFORCE', True)
    needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    resp = _match_post(_authed_client(), str(_RID + 1))  # stale release
    assert resp.status_code == 200
    assert needs.id not in {t['trialId'] for t in resp.data['results']}


@override_settings(**OMOP_TYPES)
def test_enforce_view_end_to_end_match_keeps_required_type(monkeypatch):
    from trials.services.omop import patient_release_gate as prg
    monkeypatch.setattr(prg, '_PATIENT_RELEASE_GATE_ENFORCE', True)
    needs = TrialFactory(disease='multiple myeloma', omop_therapy_types_required=[PI_CLASS])
    resp = _match_post(_authed_client(), str(_RID))  # release == active mirror
    assert resp.status_code == 200
    assert needs.id in {t['trialId'] for t in resp.data['results']}


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
