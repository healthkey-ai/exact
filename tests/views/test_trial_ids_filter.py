"""`trial_ids` — how the federated UI's Favorites tab narrows the match.

The bookmarks live in PROMOP, which knows nothing about matching. The ids
come down in the request body and the narrowing happens inside EXACT's
queryset, because that is the only place that can sort and paginate them
alongside the match scores and produce a total that agrees with the rows it
listed. Phase 2 of docs/federated-ui-parity-plan.md.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='trial-ids-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


MM = {'disease': 'multiple myeloma'}


def post(client, ids=None, path='/trials/search/match/', **extra):
    body = {'patient_info': MM, **extra}
    if ids is not None:
        body['trial_ids'] = ids
    return client.post(path, body, format='json')


@pytest.mark.django_db
class TestNarrowing:
    def test_keeps_only_the_ids_asked_for(self, authed_client):
        wanted = TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [wanted.id])
        assert response.status_code == 200
        assert [t['trialId'] for t in response.data['results']] == [wanted.id]

    def test_the_total_agrees_with_the_narrowed_set(self, authed_client):
        """Narrowing has to happen before the matcher, or `itemsTotalCount`
        counts a corpus the response does not list."""
        wanted = TrialFactory(disease='Multiple Myeloma')
        for _ in range(3):
            TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [wanted.id])
        assert response.data['itemsTotalCount'] == 1

    def test_the_tab_counts_describe_the_narrowed_set_too(self, authed_client):
        eligible = TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [eligible.id])
        counts = response.data['tabCounts']
        assert counts['eligible'] + counts['potential'] == 1

    def test_an_id_that_does_not_match_the_patient_is_still_dropped(self, authed_client):
        """The filter narrows, it does not override. A bookmarked trial for
        another disease must not reappear because its id was sent."""
        bc = TrialFactory(disease='Breast Cancer')
        mm = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [bc.id, mm.id])
        assert [t['trialId'] for t in response.data['results']] == [mm.id]

    def test_sorting_still_applies_within_the_narrowed_set(self, authed_client):
        small = TrialFactory(disease='Multiple Myeloma', enrollment_count=10)
        large = TrialFactory(disease='Multiple Myeloma', enrollment_count=900)
        response = post(
            authed_client, [small.id, large.id],
            path='/trials/search/match/?sort=enrollment',
        )
        ids = [t['trialId'] for t in response.data['results']]
        assert ids.index(large.id) < ids.index(small.id)

    def test_it_also_applies_to_the_detail_endpoint_path(self, authed_client):
        """`match_detail` shares `get_queryset`. A caller sending both an id
        in the URL and a `trial_ids` list that excludes it must get a 404,
        not a trial the filter said to leave out."""
        trial = TrialFactory(disease='Multiple Myeloma')
        other = TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': MM, 'trial_ids': [other.id]},
            format='json',
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestEmptyIsNotAbsent:
    def test_an_empty_list_returns_nothing(self, authed_client):
        """The case that matters. `[]` means "my bookmarks, of which there
        are none" — answering it with the whole corpus would show a reader
        every trial under a Favorites tab they have not used."""
        TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [])
        assert response.status_code == 200
        assert response.data['results'] == []
        assert response.data['itemsTotalCount'] == 0

    def test_omitting_the_key_is_no_filter_at_all(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client)
        assert response.data['itemsTotalCount'] == 2


@pytest.mark.django_db
class TestValidation:
    def test_a_list_longer_than_the_cap_is_refused(self, authed_client):
        """Every id becomes part of an `IN (...)`. Unbounded, this is a
        request that expands into thousands of clauses."""
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, list(range(1, 502)))
        assert response.status_code == 400
        assert 'trial_ids' in response.data

    def test_the_cap_itself_is_accepted(self, authed_client):
        trial = TrialFactory(disease='Multiple Myeloma')
        ids = [trial.id] + list(range(10_000, 10_499))
        assert len(ids) == 500
        assert post(authed_client, ids).status_code == 200

    @pytest.mark.parametrize('payload', ['1,2,3', 42, {'id': 1}])
    def test_a_non_list_is_refused(self, authed_client, payload):
        response = post(authed_client, payload)
        assert response.status_code == 400

    @pytest.mark.parametrize('bad', ['abc', None, 3.5, [1]])
    def test_an_unparseable_id_is_refused(self, authed_client, bad):
        response = post(authed_client, [1, bad])
        assert response.status_code == 400

    def test_a_boolean_is_not_a_trial_id(self, authed_client):
        """`True` is an `int` in Python and would silently become trial 1."""
        response = post(authed_client, [True])
        assert response.status_code == 400

    def test_numeric_strings_are_accepted(self, authed_client):
        """JSON from a browser often carries ids as strings."""
        trial = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [str(trial.id)])
        assert [t['trialId'] for t in response.data['results']] == [trial.id]

    def test_the_rejected_favorites_type_now_names_the_real_path(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        response = authed_client.get('/trials/search/?type=favorites')
        assert response.status_code == 400
        assert 'trial_ids' in str(response.data['type'])


@pytest.mark.django_db
class TestIdsThatLookNumericButAreNot:
    """`str.isdigit()` is not "is an integer literal".

    Guarding with it and converting afterwards is the bug: for some
    characters the guard passes and `int()` then raises, which leaves the
    view as a 500 rather than the 400 every other bad id gets.
    """

    @pytest.mark.parametrize('value', ['²', '³', '¹'])
    def test_a_superscript_digit_is_a_400_not_a_500(self, authed_client, value):
        response = post(authed_client, [value])
        assert response.status_code == 400

    @pytest.mark.parametrize('value', ['--5', '- 5', '+5'])
    def test_a_malformed_sign_is_refused(self, authed_client, value):
        response = post(authed_client, [value])
        assert response.status_code == 400

    def test_fullwidth_digits_are_refused(self, authed_client):
        """`int('１２３')` succeeds and yields 123 — a different trial from
        the literal text the caller sent."""
        assert post(authed_client, ['１２３']).status_code == 400

    def test_a_negative_id_is_refused(self, authed_client):
        """A primary key is never negative, and admitting a sign is what let
        `'--5'` through."""
        assert post(authed_client, [-1]).status_code == 400
        assert post(authed_client, ['-1']).status_code == 400

    def test_an_absurdly_long_digit_string_is_a_400(self, authed_client):
        """Python refuses to parse an int past a digit limit, which would
        have escaped as a 500 too."""
        assert post(authed_client, ['9' * 5000]).status_code == 400

    def test_the_largest_id_the_column_can_hold_is_just_a_miss(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [2 ** 63 - 1])
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 0

    @pytest.mark.parametrize('value', [2 ** 63, 10 ** 18 * 10, '9999999999999999999'])
    def test_an_id_beyond_the_bigint_range_is_refused(self, authed_client, value):
        """Not pedantry about a value that would not match anyway: above the
        bigint range PostgreSQL types the constant as `numeric`, coerces the
        `id` column to compare it, and gives up the primary-key index. A
        list of 500 of those is a table scan in the shape of a bookmarks
        lookup."""
        assert post(authed_client, [value]).status_code == 400


@pytest.mark.django_db
class TestQueryStringIsNotTheChannel:
    def test_trial_ids_in_the_query_string_is_refused(self, authed_client):
        """Ignored, it answers a bookmarks question with the whole corpus —
        the same wrong answer an empty list is guarded against, reached
        through a different door. The body is the channel."""
        TrialFactory(disease='Multiple Myeloma')
        response = authed_client.get('/trials/search/?trial_ids=1,2')
        assert response.status_code == 400
        assert 'trial_ids' in response.data

    def test_the_body_still_works_with_a_query_string_present(self, authed_client):
        trial = TrialFactory(disease='Multiple Myeloma')
        response = post(
            authed_client, [trial.id], path='/trials/search/match/?sort=distance'
        )
        assert response.status_code == 200
