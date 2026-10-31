"""Release-consistency validator tests (#286).

validate_concept_ids returns the subset of concept_ids that are PRESENT and VALID
(invalid_reason IS NULL) in the mirror at the given release. release_id is required
(None -> fail closed). Per-concept: a stale/absent concept drops out; the rest stay.
"""
import pytest

from vocab_mirror.models import MirrorConcept
from vocab_mirror.traversal import ConceptGraphUnavailable
from vocab_mirror.validate import validate_concept_ids

pytestmark = pytest.mark.django_db

RID = 1


def _concept(concept_id, invalid_reason=None, release_id=RID):
    MirrorConcept.objects.create(
        release_id=release_id, concept_id=concept_id, concept_name=f'c{concept_id}',
        domain_id='Drug', vocabulary_id='HemOnc', concept_class_id='Component Class',
        concept_code=str(concept_id), invalid_reason=invalid_reason)


def test_present_and_valid_kept():
    _concept(35807295)
    assert validate_concept_ids(['35807295'], RID) == {35807295}


def test_invalidated_concept_dropped():
    _concept(35807295, invalid_reason='D')      # deprecated
    assert validate_concept_ids(['35807295'], RID) == set()


def test_absent_concept_dropped():
    # nothing created -> concept not in mirror
    assert validate_concept_ids(['35807295'], RID) == set()


def test_mixed_keeps_only_valid_present():
    _concept(35807295)                          # valid
    _concept(35807403, invalid_reason='U')      # invalid
    # 35807345 absent
    assert validate_concept_ids(['35807295', '35807403', '35807345'], RID) == {35807295}


def test_release_scoped_other_release_not_seen():
    _concept(35807295, release_id=2)            # valid but in a DIFFERENT release
    assert validate_concept_ids(['35807295'], RID) == set()


def test_none_release_fails_closed():
    _concept(35807295)
    with pytest.raises(ConceptGraphUnavailable):
        validate_concept_ids(['35807295'], None)


def test_empty_and_non_digit_input():
    assert validate_concept_ids([], RID) == set()
    assert validate_concept_ids(None, RID) == set()
    assert validate_concept_ids(['abc', '0', '-5', None], RID) == set()
