"""Per-request release pin + mirror title resolution tests (#252 / ADR 0002)."""
import pytest
from django.test import override_settings

from vocab_mirror.activation import activate_release
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)
from vocab_mirror.release_context import (
    MatchingReleaseContext,
    _pinned_release,
    active_pinned_release,
)

pytestmark = pytest.mark.django_db


def _activate(rid=5, concepts=()):
    MirrorVocabulary.objects.create(release_id=rid, vocabulary_id='V', vocabulary_name='V',
                                    vocabulary_concept_id=1)
    MirrorConceptRelationship.objects.create(release_id=rid, concept_id_1=1, concept_id_2=2,
                                             relationship_id='seed')
    MirrorConceptAncestor.objects.create(release_id=rid, ancestor_concept_id=1, descendant_concept_id=2,
                                         min_levels_of_separation=1, max_levels_of_separation=1)
    MirrorConcept.objects.create(release_id=rid, concept_id=1, concept_name='c', domain_id='D',
                                 vocabulary_id='V', concept_class_id='C', concept_code='1')
    for cid, name, vocab in concepts:
        MirrorConcept.objects.create(release_id=rid, concept_id=cid, concept_name=name,
                                     domain_id='D', vocabulary_id=vocab, concept_class_id='C',
                                     concept_code=str(cid))
    MirrorRelease.objects.create(release_id=rid, state=MirrorRelease.READY)
    activate_release(rid)


class TestActivePinnedRelease:
    def test_falls_back_to_active_release_when_unpinned(self):
        _activate(5)
        assert active_pinned_release() == 5  # no context -> active_release_id()

    def test_none_when_nothing_active_and_unpinned(self):
        assert active_pinned_release() is None

    def test_context_pin_wins_over_active(self):
        _activate(5)
        token = _pinned_release.set(99)
        try:
            assert active_pinned_release() == 99
        finally:
            _pinned_release.reset(token)


class TestMatchingReleaseContext:
    @override_settings(EXACT_OMOP_THERAPY=False)
    def test_omop_off_pins_none_and_no_header(self):
        _activate(5)  # active release exists, but OMOP is off
        with MatchingReleaseContext() as ctx:
            assert ctx.omop_active is False
            assert ctx.release_id is None
            assert ctx.header is None
            # pinned to None (not the sentinel) -> readers get None, NOT the live 5
            assert active_pinned_release() is None

    @override_settings(EXACT_OMOP_THERAPY=True)
    def test_omop_on_pins_active_release_and_sets_header(self):
        _activate(5)
        with MatchingReleaseContext() as ctx:
            assert ctx.omop_active is True
            assert ctx.release_id == 5
            assert ctx.header == '5'
            assert active_pinned_release() == 5
        # pin is reset after the block -> unpinned, falls back to active_release_id()
        assert active_pinned_release() == 5

    @override_settings(EXACT_OMOP_THERAPY=True)
    def test_omop_on_no_active_release_pins_none(self):
        with MatchingReleaseContext() as ctx:
            assert ctx.release_id is None
            assert ctx.header is None
            assert active_pinned_release() is None


class TestMirrorTitleResolution:
    """_resolve_omop_concepts reads the release-pinned mirror, fail-soft."""

    def _resolve(self, *a, **kw):
        from trials.services.user_to_trial_attr_matcher import _resolve_omop_concepts
        return _resolve_omop_concepts(*a, **kw)

    def test_no_active_release_is_code_only(self):
        assert self._resolve([100]) == [{'code': 100, 'title': None, 'vocab': None}]

    def test_active_release_resolves_titles(self):
        _activate(5, concepts=[(100, 'Bortezomib', 'RxNorm')])
        assert self._resolve([100]) == [{'code': 100, 'title': 'Bortezomib', 'vocab': 'RxNorm'}]

    def test_unresolved_concept_in_active_release_is_code_only(self):
        _activate(5, concepts=[(100, 'Bortezomib', 'RxNorm')])
        assert self._resolve([999]) == [{'code': 999, 'title': None, 'vocab': None}]

    def test_explicit_release_id_overrides_the_pin(self):
        _activate(5, concepts=[(100, 'active-name', 'RxNorm')])
        MirrorConcept.objects.create(release_id=7, concept_id=100, concept_name='other-release',
                                     domain_id='D', vocabulary_id='HemOnc', concept_class_id='C',
                                     concept_code='100')
        assert self._resolve([100], release_id=7) == [
            {'code': 100, 'title': 'other-release', 'vocab': 'HemOnc'}]
