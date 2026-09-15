"""The search endpoint must survive a filter that narrows through a join.

Regression: QA on cb-like-trials, 2026-09-14.
Report: .gstack/qa-reports/qa-report-localhost-5201-2026-09-14.md

`BlankAttributeRecordsCount` builds its aggregations from CASE-WHEN strings
that name columns UNQUALIFIED. That resolves against the table; it stops
resolving once the scope carries a `.distinct()`, because Django then computes
`aggregate()` over a derived table whose select list lacks those columns, and
Postgres answers `column "age_low_limit" does not exist`.

Every search with a resolvable country produced exactly that scope, so the
endpoint 500'd — measured against a real backend, `?country=US` returned 500
while the same body without it returned 200. The federated remote derives
`country` from `patientInfo.country` and sends it on every request, so this
was the primary flow, not an edge.

The unit test beside the service covers the mechanism on a synthetic
`.distinct()`. These go through HTTP, because the reason 1290 green tests
missed it is that NOTHING went through HTTP with a narrowing filter: the 16
tests in `tests/querysets/test_country_filter.py` all stop at the queryset.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from trials.models import Country, Location, LocationTrial, PreferredCountry
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='narrowing-filter-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


PATIENT = {'patient_info': {'disease': 'multiple myeloma'}}

# `with_distance_optimized` only engages for a patient the matcher can place,
# and `geo_point` is derived — from country+postal code, or from raw
# coordinates when neither is given. Without it the `?distance=` and
# `?type=all` requests take the plain path and prove nothing.
PLACED_PATIENT = {
    'patient_info': {
        'disease': 'multiple myeloma',
        'longitude': -73.9857,
        'latitude': 40.7484,
    }
}


@pytest.fixture
def sited_catalog(db):
    """A country the filter can actually resolve, with a trial sited in it.

    Without this the test is VACUOUS and silently so: `_country_ids_for('US')`
    reads `PreferredCountry`, finds nothing, and `by_location` returns the
    scope unchanged — no join, no `.distinct()`, no 500 to catch. The first
    version of this file had no catalog and passed with BOTH fixes reverted.
    """
    usa = Country.objects.create(title='United States of America')
    PreferredCountry.objects.create(code='US', title='United States of America')
    trial = TrialFactory(disease='Multiple Myeloma', age_low_limit=18)
    LocationTrial.objects.create(
        trial=trial,
        location=Location.objects.create(
            city='new york', title='Site NY', country=usa,
        ),
    )
    return trial


@pytest.mark.django_db
class TestSearchWithANarrowingFilter:
    def test_a_country_filter_does_not_500(self, authed_client, sited_catalog):
        response = authed_client.post(
            '/trials/search/match/?country=US', PATIENT, format='json'
        )
        assert response.status_code == 200, response.data

    def test_a_country_and_region_filter_does_not_500(self, authed_client, sited_catalog):
        response = authed_client.post(
            '/trials/search/match/?country=US&region=New York', PATIENT, format='json'
        )
        assert response.status_code == 200, response.data

    def test_a_distance_filter_does_not_500(self, authed_client, sited_catalog):
        """`with_distance_optimized` also ends in `.distinct()` and is reached
        without any country — so the same 500 is available through `?distance=`
        and `?type=all`. Those paths exist verbatim on `main` and `dev`, where
        nothing fixes them.
        """
        response = authed_client.post(
            '/trials/search/match/?distance=100&distanceUnits=miles',
            PLACED_PATIENT,
            format='json',
        )
        assert response.status_code == 200, response.data

    def test_type_all_does_not_500(self, authed_client, sited_catalog):
        response = authed_client.post(
            '/trials/search/match/?type=all', PLACED_PATIENT, format='json'
        )
        assert response.status_code == 200, response.data
