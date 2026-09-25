"""The trials page's own store: who a row belongs to, and who can reach it.

What is worth testing here is not the CRUD. It is the three conditions rule 5
now puts on any per-user table in EXACT — the key comes from the token, the
row lives on `default`, and erasure exists — plus the one thing that makes the
key safe in practice: there is no route that can name somebody else.
"""
import pytest
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from rest_framework.authtoken.models import Token
from user_state.models import TrialSearchPreferences, forget_identity


def client_for(sub):
    identity, _ = Identity.objects.get_or_create(issuer='urn:local', sub=sub)
    token, _ = Token.objects.get_or_create(user=identity)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return identity, client


@pytest.fixture
def authed(db):
    return client_for('prefs-owner')


PREFS = '/user-state/trial-search-preferences/'


@pytest.mark.django_db
class TestWhoTheRowBelongsTo:
    def test_unauthenticated_gets_nothing(self):
        assert APIClient().get(PREFS).status_code in (401, 403)

    def test_the_row_is_keyed_on_the_token_identity(self, authed):
        identity, client = authed
        client.post(PREFS, {'preferences': {'phase': 'II'}}, format='json')

        row = TrialSearchPreferences.objects.get()
        assert row.identity_id == identity.pk

    def test_two_identities_do_not_share_a_row(self, db):
        _, mine = client_for('reader-a')
        _, theirs = client_for('reader-b')
        mine.post(PREFS, {'preferences': {'phase': 'II'}}, format='json')

        assert theirs.get(PREFS).data['preferences'] == {}
        assert mine.get(PREFS).data['preferences'] == {'phase': 'II'}

    def test_no_route_can_name_another_identity(self, db):
        # The guarantee is structural, not a permission check somebody has to
        # remember: a route with no id in it cannot be pointed at a stranger.
        # `?person_id=` is the cautionary tale — see `exact/settings.py`.
        with pytest.raises(NoReverseMatch):
            reverse('user_state:trial-search-preferences-detail', args=[1])

        # And no URL that reaches one either, named or typed by hand.
        with pytest.raises(Resolver404):
            resolve(PREFS + '1/')


@pytest.mark.django_db
class TestWhereTheRowLives:
    def test_it_is_not_routed_to_the_trials_database(self, settings):
        # The `trials` alias is dropped and restored from CB dumps ("must DROP
        # all schemas"), so a user-scoped row there disappears at the next
        # restore.
        #
        # Asked of the ROUTER CHAIN, not of our router object, and with the
        # alias actually configured. The first version of this test did
        # neither: test settings define only `default`, so our router returns
        # None for everything, and `None` means "no opinion" rather than
        # "default" — it passed for a `trials` model too, and would pass with
        # a second router in the chain sending these rows to the wrong place.
        from django.db import router as chain

        from trials.models import Trial

        settings.DATABASES = {
            **settings.DATABASES,
            'trials': dict(settings.DATABASES['default']),
        }

        assert chain.db_for_write(TrialSearchPreferences) == 'default'
        # The contrast is the point: with the alias up, a trials model DOES
        # go there, so this is measuring the branch rather than its absence.
        assert chain.db_for_write(Trial) == 'trials'


@pytest.mark.django_db
class TestWhatItStores:
    def test_reading_before_writing_answers_defaults(self, authed):
        _, client = authed
        body = client.get(PREFS).data

        assert body['preferences'] == {}
        assert body['weights_wizard_offered'] is False
        assert body['non_default_filter_count'] == 0

    def test_reading_does_not_write(self, authed):
        # EXACT's first row about somebody must not appear because they opened
        # a page. It is also what makes GET safe and idempotent — cacheable,
        # and servable from a replica.
        _, client = authed

        assert client.get(PREFS).status_code == 200
        assert not TrialSearchPreferences.objects.exists()

    def test_a_deeply_nested_payload_is_refused_rather_than_crashing(self, authed):
        # 100 KB, far under Django's 2.5 MB body limit, and `JSONParser`
        # recurses building it: unbounded, this is a 500 at 300 requests a
        # minute. The cap cannot fix the parser — every JSON endpoint shares
        # that — but this column is the one aimed at.
        _, client = authed
        nested = {'a': [[[['too deep']]]]}

        assert client.post(PREFS, {'preferences': nested}, format='json').status_code == 400

    def test_a_non_object_payload_is_refused(self, authed):
        _, client = authed
        assert client.post(PREFS, {'preferences': ['phase']}, format='json').status_code == 400

    def test_it_refuses_a_payload_that_is_not_a_filter_set(self, authed):
        # `StudyPreferences` has 20 fields and the page adds `sort` and
        # `type`; both caps are far above that, so a reader never meets them.
        # They exist because this is the first writable surface in EXACT and
        # an opaque column with no bound is a place to put anything.
        _, client = authed
        too_many = {f'filter{i}': 'x' for i in range(65)}
        too_big = {'searchTitle': 'x' * (16 * 1024 + 1)}

        assert client.post(PREFS, {'preferences': too_many}, format='json').status_code == 400
        assert client.post(PREFS, {'preferences': too_big}, format='json').status_code == 400
        assert not TrialSearchPreferences.objects.filter(
            preferences__has_key='filter0'
        ).exists()

    def test_the_count_ignores_the_tab_and_the_sort(self, authed):
        # Ported verbatim from PROMOP's copy, which answers the same badge
        # while both stores exist. Switching tab must not tick it up.
        _, client = authed
        body = client.post(
            PREFS,
            {'preferences': {'phase': 'II', 'sort': 'distance', 'type': 'all',
                             'searchTitle': '', 'nearMe': False, 'distance': 0}},
            format='json',
        ).data

        assert body['non_default_filter_count'] == 1

    def test_reset_clears_filters_and_leaves_the_wizard_answered(self, authed):
        _, client = authed
        client.post(
            PREFS,
            {'preferences': {'phase': 'II'}, 'weights_wizard_offered': True},
            format='json',
        )

        body = client.post(PREFS + 'reset/', {}, format='json').data

        assert body['preferences'] == {}
        # "Reset my filters" is a sentence about filters. Offering the wizard
        # again because of it would be a surprise, and the flag exists to make
        # that offer exactly once.
        assert body['weights_wizard_offered'] is True


@pytest.mark.django_db
class TestWhoCanReachIt:
    def test_a_service_token_cannot_write_human_state(self, db, settings):
        # The project's default auth chain puts the service token FIRST, so
        # without pinning, any service holding the shared secret writes a row
        # as `urn:service`: a machine credential on a human-state surface,
        # shared by every holder, and one erasure is never called for.
        settings.SERVICE_AUTH_TOKEN = 'svc-secret'
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer svc-secret')

        assert client.get(PREFS).status_code in (401, 403)
        assert not TrialSearchPreferences.objects.exists()


@pytest.mark.django_db
class TestErasure:
    def test_it_empties_every_table_this_app_owns(self, db):
        # Enumerated, not listed. Say plainly what this is: with one model in
        # the app it CANNOT fail today — a hardcoded implementation passes it
        # too. It is a tripwire for phase 2 and phase 3, which add favourites
        # and registration interest, and it fires the day one of them lands
        # unwired, which is exactly when nobody will be looking at erasure.
        # Do not read it as coverage of the code as it stands.
        from django.apps import apps

        identity, client = client_for('prefs-owner')
        client.post(PREFS, {'preferences': {'phase': 'II'}}, format='json')
        models = list(apps.get_app_config('user_state').get_models())
        assert models, 'the app has no models; this test would prove nothing'

        result = forget_identity('urn:local', 'prefs-owner')

        assert result['found'] is True
        assert set(result['removed']) == {m._meta.db_table for m in models}
        for model in models:
            assert not model.objects.filter(identity=identity).exists()

    def test_it_takes_the_identity_and_its_tokens_too(self, authed):
        # Leaving them is not tidiness. `Identity` itself records that this
        # person exists here, and a live token lets an in-flight request write
        # the row back moments after it was erased.
        identity, client = authed
        client.post(PREFS, {'preferences': {'phase': 'II'}}, format='json')

        forget_identity('urn:local', 'prefs-owner')

        assert not Identity.objects.filter(pk=identity.pk).exists()
        assert not Token.objects.filter(user_id=identity.pk).exists()

    def test_it_tells_never_existed_from_already_gone(self, db):
        # A sweep re-running over identities PROMOP no longer knows should be
        # able to say which of the two it found, rather than logging "deleted
        # 0" for both.
        assert forget_identity('urn:local', 'never-here') == {
            'found': False, 'removed': {},
        }

    def test_the_offline_path_works_without_a_token_or_promop(self, authed):
        # The endpoint needs SERVICE_AUTH_TOKEN configured and PROMOP calling
        # it. A right to erasure that a missing environment variable can block
        # is not one, so there is a way to do it by hand.
        from django.core.management import call_command

        _, client = authed
        client.post(PREFS, {'preferences': {'phase': 'II'}}, format='json')

        call_command('forget_identity', issuer='urn:local', sub='prefs-owner')

        assert not TrialSearchPreferences.objects.exists()

    def test_the_offline_path_refuses_to_claim_it_deleted_nothing(self, db):
        # An issuer typo must not read as success. This is the shape of
        # mistake that leaves rows behind and a log line saying they are gone.
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command('forget_identity', issuer='urn:typo', sub='nobody')

    def test_the_endpoint_erases_for_a_service_token(self, authed, settings):
        # The happy path PROMOP will actually call, over HTTP. Nothing tested
        # it: the only endpoint test asserted a 400 for a missing field.
        settings.SERVICE_AUTH_TOKEN = 'svc-secret'
        _, owner = authed
        owner.post(PREFS, {'preferences': {'phase': 'II'}}, format='json')

        service = APIClient()
        service.credentials(HTTP_AUTHORIZATION='Bearer svc-secret')
        resp = service.post(
            '/user-state/internal/forget/',
            {'issuer': 'urn:local', 'sub': 'prefs-owner'},
            format='json',
        )

        assert resp.status_code == 200
        assert resp.data['found'] is True
        assert not TrialSearchPreferences.objects.exists()

    def test_the_endpoint_refuses_an_end_user_token(self, authed):
        # Erasing somebody is PROMOP's call, not a reader's. Asserted on the
        # row surviving as well as on the status: a 403 that deleted first
        # would pass a status-only check.
        _, client = authed
        client.post(PREFS, {'preferences': {'phase': 'II'}}, format='json')

        resp = client.post(
            '/user-state/internal/forget/',
            {'issuer': 'urn:local', 'sub': 'prefs-owner'},
            format='json',
        )

        assert resp.status_code in (401, 403)
        assert TrialSearchPreferences.objects.count() == 1

    def test_the_endpoint_needs_both_halves_of_the_identity(self, db, settings):
        settings.SERVICE_AUTH_TOKEN = 'svc-secret'
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer svc-secret')

        resp = client.post('/user-state/internal/forget/', {'issuer': 'urn:local'}, format='json')

        assert resp.status_code == 400
