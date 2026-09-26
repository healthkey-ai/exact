"""The credential these routes actually run on in production.

Every other test in this app authenticates with a DRF token, which exists
only where `ENABLE_DRF_TOKEN_AUTH` is on — local and DEBUG, gated off
everywhere else by #153. `exact/test_settings.py` forces `ENVIRONMENT=local`,
so the whole suite was exercising a credential that staging and production
reject, and after `authentication_classes` was pinned, `PartnerAuthentication`
became the only thing standing between these routes and a 401 out there. Run
the suite with the flag off and ten tests 401.

So: the same routes, over a verified partner token, with the token path
faked the way `tests/test_auth.py` fakes it.
"""
import pytest
from rest_framework.test import APIClient

from accounts.models import Identity
from accounts.providers.base import TokenClaims
from user_state.models import TrialSearchPreferences

PREFS = '/user-state/trial-search-preferences/'


class _FakeProvider:
    ISSUER = 'https://securetoken.google.com/exact-test'
    SUB = 'firebase-uid-123'

    def can_handle(self, token, unverified_payload):
        return True

    def verify(self, token):
        return TokenClaims(
            issuer=self.ISSUER, sub=self.SUB,
            email='patient@example.com', name='Pat Example', raw={},
        )


@pytest.fixture
def partner_client(db, monkeypatch, settings):
    # With the flag off, as staging and production run it: proves the routes
    # work on the partner token alone rather than falling through to DRF's.
    #
    # This only became true when `authentication_classes` stopped being a
    # class attribute: evaluated at import, the override below changed
    # nothing and this fixture was quietly testing the local configuration.
    settings.ENABLE_DRF_TOKEN_AUTH = False
    monkeypatch.setattr(
        'accounts.authentication.get_providers', lambda: [_FakeProvider()],
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION='Bearer fake.partner.jwt')
    return client


@pytest.mark.django_db
class TestOverAPartnerToken:
    def test_the_routes_work_on_the_credential_production_uses(self, partner_client):
        assert partner_client.get(PREFS).status_code == 200

        saved = partner_client.post(
            PREFS, {'preferences': {'phase': 'II'}}, format='json',
        )

        assert saved.status_code == 200
        assert partner_client.get(PREFS).data['preferences'] == {'phase': 'II'}

    def test_the_row_belongs_to_the_identity_in_the_token(self, partner_client):
        partner_client.post(PREFS, {'preferences': {'phase': 'II'}}, format='json')

        identity = Identity.objects.get(
            issuer=_FakeProvider.ISSUER, sub=_FakeProvider.SUB,
        )
        assert TrialSearchPreferences.objects.get().identity_id == identity.pk

    def test_the_drf_token_is_not_accepted_where_it_is_gated_off(self, db, settings):
        # The other half, and the one that proves the fixture above is doing
        # what it says: the credential every other test in this app uses must
        # NOT work in a deployed configuration. If this passes while the flag
        # is honoured, the pinned list is real.
        from rest_framework.authtoken.models import Token

        settings.ENABLE_DRF_TOKEN_AUTH = False
        identity, _ = Identity.objects.get_or_create(issuer='urn:local', sub='dev')
        token, _ = Token.objects.get_or_create(user=identity)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        assert client.get(PREFS).status_code in (401, 403)

    def test_a_bad_partner_token_reaches_nothing(self, db, monkeypatch, settings):
        settings.ENABLE_DRF_TOKEN_AUTH = False
        monkeypatch.setattr('accounts.authentication.get_providers', lambda: [])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer not.a.real.jwt')

        assert client.get(PREFS).status_code in (401, 403)
        assert not TrialSearchPreferences.objects.exists()


@pytest.mark.django_db
class TestTheErasureWarning:
    """`user_state.W001` — loud where it matters, quiet where it does not."""

    def test_it_fires_where_promop_would_be_calling(self, settings, monkeypatch):
        from user_state.apps import check_erasure_is_reachable

        settings.SERVICE_AUTH_TOKEN = ''
        settings.DEBUG = False
        monkeypatch.setenv('ENVIRONMENT', 'staging')

        assert [w.id for w in check_erasure_is_reachable(None)] == ['user_state.W001']

    @pytest.mark.parametrize('debug,environment', [(True, 'staging'), (False, 'local')])
    def test_it_is_quiet_on_a_laptop(self, settings, monkeypatch, debug, environment):
        # `.env.example` ships the secret empty and nothing calls the endpoint
        # locally, so firing here would put the warning in front of every
        # developer and every CI run until nobody reads it.
        #
        # Both arms, separately. With one case the DEBUG arm was covered by
        # accident of `ENVIRONMENT=local` in the test settings and reachable
        # from no test — the same shape as the import-time authentication
        # attribute this file exists to keep honest.
        from user_state.apps import check_erasure_is_reachable

        settings.SERVICE_AUTH_TOKEN = ''
        settings.DEBUG = debug
        monkeypatch.setenv('ENVIRONMENT', environment)

        assert check_erasure_is_reachable(None) == []

    def test_a_configured_secret_silences_it(self, settings, monkeypatch):
        from user_state.apps import check_erasure_is_reachable

        settings.SERVICE_AUTH_TOKEN = 'svc-secret'
        settings.DEBUG = False
        monkeypatch.setenv('ENVIRONMENT', 'staging')

        assert check_erasure_is_reachable(None) == []
