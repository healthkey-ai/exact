"""API-level test for the X-Exact-OMOP-Release response header (#252).

The header names the vocab-mirror release a response's OMOP titles were resolved
from — present only when OMOP mode is on AND a release is active.
"""
import pytest
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory
from vocab_mirror.activation import activate_release
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)

HEADER = 'X-Exact-OMOP-Release'


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='hdr-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


def _activate(rid=3):
    MirrorVocabulary.objects.create(release_id=rid, vocabulary_id='V', vocabulary_name='V',
                                    vocabulary_concept_id=1)
    MirrorConcept.objects.create(release_id=rid, concept_id=1, concept_name='c', domain_id='D',
                                 vocabulary_id='V', concept_class_id='C', concept_code='1')
    MirrorConceptRelationship.objects.create(release_id=rid, concept_id_1=1, concept_id_2=2,
                                             relationship_id='seed')
    MirrorConceptAncestor.objects.create(release_id=rid, ancestor_concept_id=1, descendant_concept_id=2,
                                         min_levels_of_separation=1, max_levels_of_separation=1)
    MirrorRelease.objects.create(release_id=rid, state=MirrorRelease.READY)
    activate_release(rid)


@override_settings(EXACT_OMOP_THERAPY=True)
def test_header_present_when_omop_on_with_active_release(authed_client):
    _activate(3)
    TrialFactory(disease='Multiple Myeloma')
    resp = authed_client.get('/trials/')
    assert resp.status_code == 200
    assert resp[HEADER] == '3'


@override_settings(EXACT_OMOP_THERAPY=True)
def test_header_absent_when_no_active_release(authed_client):
    TrialFactory(disease='Multiple Myeloma')
    resp = authed_client.get('/trials/')
    assert resp.status_code == 200
    assert HEADER not in resp


@override_settings(EXACT_OMOP_THERAPY=False)
def test_header_absent_when_omop_off(authed_client):
    _activate(3)  # a release is active, but OMOP matching is off
    TrialFactory(disease='Multiple Myeloma')
    resp = authed_client.get('/trials/')
    assert resp.status_code == 200
    assert HEADER not in resp
