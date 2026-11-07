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
from trials.api.trials_views import TrialsViewSet
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
    def test_no_radius_asked_for_means_no_key(self, client):
        response = client.get('/trials/')
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

    @pytest.mark.parametrize('path', ['/trials/search/', '/trials/count/'])
    def test_search_and_count_say_it_too(self, client, path):
        response = client.get(f'{path}?{RADIUS}')
        assert response.status_code == 200
        assert response.json()['distanceFilter']['applied'] is False
