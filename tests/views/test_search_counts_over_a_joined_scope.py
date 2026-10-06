"""`GET /trials/search/` must survive a filter that narrows through a join.

Regression for #490. `BlankAttributeRecordsCount` names columns UNQUALIFIED in
its CASE-WHEN strings, which stops resolving once the scope carries a
`.distinct()`: Django then aggregates over a derived table whose select list
lacks them, and Postgres answers `column "age_low_limit" does not exist`.

`_trials_counts` runs under the `search` action, and both `by_location` and
`with_distance_optimized` end in `.distinct()`, so the endpoint 500s. The
second needs no country at all — only a patient the matcher can place.

Found by QA on `cb-like-trials` (PR #489); this is the same defect on `dev`,
where nothing on the branch is involved.
"""
import json

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from trials.models import Country, Location, LocationTrial, PreferredCountry
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='counts-joined-scope')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.fixture
def sited_catalog(db):
    """A country the filter can resolve, with a trial sited in it.

    Required by the COUNTRY test only: without it `by_location` resolves
    nothing, returns the scope unchanged, and that one test is vacuous.
    Measured — removing this fixture leaves the distance and `type=all` tests
    still failing against unfixed code, because `with_distance_optimized`
    reaches `.distinct()` from the patient's `geo_point` alone. That is the
    whole reason those two are here: they need no country at all.
    """
    usa = Country.objects.create(title='United States of America')
    PreferredCountry.objects.create(code='US', title='United States of America')
    trial = TrialFactory(disease='Multiple Myeloma', age_low_limit=18)
    LocationTrial.objects.create(
        trial=trial,
        location=Location.objects.create(city='new york', title='Site NY', country=usa),
    )
    return trial


def _search(client, query, patient):
    # GET-with-body: `resolve_patient_info` reads `patient_info` from the body,
    # and `search` is GET-only on this branch.
    return client.generic(
        'GET',
        f'/trials/search/?{query}',
        json.dumps(patient),
        content_type='application/json',
    )


PLACED = {'patient_info': {'disease': 'multiple myeloma',
                           'longitude': -73.9857, 'latitude': 40.7484}}
PLAIN = {'patient_info': {'disease': 'multiple myeloma'}}


@pytest.mark.django_db
class TestSearchCountsOverAJoinedScope:
    def test_a_country_filter_does_not_500(self, authed_client, sited_catalog):
        r = _search(authed_client, 'country=United States of America', PLAIN)
        assert r.status_code == 200, r.data

    def test_a_distance_filter_does_not_500(self, authed_client, sited_catalog):
        r = _search(authed_client, 'distance=100&distanceUnits=miles', PLACED)
        assert r.status_code == 200, r.data

    def test_type_all_does_not_500(self, authed_client, sited_catalog):
        r = _search(authed_client, 'type=all', PLACED)
        assert r.status_code == 200, r.data
