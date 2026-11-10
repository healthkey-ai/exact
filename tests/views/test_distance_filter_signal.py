"""A radius that could not be applied is said so in the response (#475).

Without a patient location the queryset skips the distance filter and falls
through to country/region, which is no filter when no country was sent: a
caller asking for trials within 50 miles got the whole corpus with a 200 and
nothing to tell them. `distanceFilter` now says whether the radius was applied.
"""
from unittest import mock

import pytest
from django.contrib.gis.geos import Point
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import LocationFactory, TrialFactory
from trials.api.trials_views import TrialsViewSet
from trials.models import LocationTrial
from trials.services.patient_info.resolve import _build_in_memory

RADIUS = 'distance=50&distanceUnits=miles'
PATIENT = {'disease': 'multiple myeloma'}


@pytest.fixture
def client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='distance-475-tester')
    token, _ = Token.objects.get_or_create(user=user)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return api


@pytest.mark.django_db
class TestDistanceFilterSignal:
    @pytest.mark.parametrize('query', ['', '?distance=', '?distance=%20'])
    def test_no_radius_asked_for_means_no_key(self, client, query):
        response = client.get(f'/trials/{query}')
        assert response.status_code == 200
        assert 'distanceFilter' not in response.json()

    def test_a_radius_without_any_patient_was_not_applied(self, client):
        response = client.get(f'/trials/?{RADIUS}')
        assert response.status_code == 200
        assert response.json()['distanceFilter'] == {'applied': False, 'reason': 'no_patient'}

    def test_a_radius_for_a_patient_without_a_location_was_not_applied(self, client):
        response = client.post(
            f'/trials/match/?{RADIUS}', {'patient_info': PATIENT}, format='json',
        )
        assert response.status_code == 200
        assert response.json()['distanceFilter'] == {
            'applied': False, 'reason': 'no_patient_location',
        }

    def test_a_radius_for_a_located_patient_was_applied(self, client):
        located = _build_in_memory(dict(PATIENT))
        located.geo_point = Point(-71.06, 42.36, srid=4326)
        with mock.patch.object(TrialsViewSet, '_resolve_patient_info', return_value=located):
            response = client.get(f'/trials/?{RADIUS}')
        assert response.status_code == 200
        assert response.json()['distanceFilter'] == {'applied': True}

    @pytest.mark.parametrize('value', ['0', '-5', '-0.1', 'abc', 'nan', 'inf'])
    def test_a_radius_that_is_not_positive_was_not_applied(self, client, value):
        # It takes the radius branch, so country/region is skipped too, yet
        # `by_distance` returns early on `distance <= 0`: no filter at all.
        response = client.get(f'/trials/?distance={value}&distanceUnits=miles')
        assert response.json()['distanceFilter'] == {
            'applied': False, 'reason': 'invalid_distance',
        }

    @pytest.mark.parametrize('path', ['/trials/search/', '/trials/count/'])
    def test_search_and_count_say_it_too(self, client, path):
        response = client.get(f'{path}?{RADIUS}')
        assert response.status_code == 200
        assert response.json()['distanceFilter']['applied'] is False


BOSTON = Point(-71.06, 42.36, srid=4326)


@pytest.fixture
def near_and_far(db):
    """One trial 5 km from the patient, one an ocean away."""
    near = TrialFactory()
    far = TrialFactory()
    for trial, title, point in (
        (near, 'cambridge-475', Point(-71.11, 42.37, srid=4326)),
        (far, 'lisbon-475', Point(-9.14, 38.72, srid=4326)),
    ):
        location = LocationFactory(title=title)
        location.geo_point = point
        location.save()
        LocationTrial.objects.create(trial=trial, location=location, recruitment_status='RECRUITING')
    return near, far


def _ids(response):
    return {row['trialId'] for row in response.json()['results']}


@pytest.mark.django_db
class TestTheFlagMatchesTheRows:
    """`applied` is a claim about the rows; check it against them."""

    def _located(self):
        patient = _build_in_memory(dict(PATIENT))
        patient.geo_point = BOSTON
        return mock.patch.object(TrialsViewSet, '_resolve_patient_info', return_value=patient)

    def test_applied_means_the_far_trial_is_gone(self, client, near_and_far):
        near, far = near_and_far
        with self._located():
            response = client.get('/trials/search/?distance=50&distanceUnits=miles')
        assert response.json()['distanceFilter'] == {'applied': True}
        assert near.id in _ids(response) and far.id not in _ids(response)

    def test_the_all_search_keeps_the_far_trial_and_says_so(self, client, near_and_far):
        near, far = near_and_far
        with self._located():
            response = client.get('/trials/search/?type=all&distance=50&distanceUnits=miles')
        assert response.json()['distanceFilter'] == {'applied': False, 'reason': 'search_type'}
        assert {near.id, far.id} <= _ids(response)

    def test_not_applied_means_the_far_trial_is_still_there(self, client, near_and_far):
        near, far = near_and_far
        response = client.post(
            '/trials/search/match/?distance=50&distanceUnits=miles',
            {'patient_info': PATIENT}, format='json',
        )
        assert response.json()['distanceFilter']['applied'] is False
        assert {near.id, far.id} <= _ids(response)
