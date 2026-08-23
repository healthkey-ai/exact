"""Tests for PromopClient — the thin HTTP layer to PROMOP's patient endpoint (#102).

All scenarios verify the same contract: `fetch_patient` either returns
the parsed JSON dict or `None`. It must never raise — the resolver
treats failures the same as a missing payload.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from trials.services.patient_info.promop_client import PromopClient


def _ok_response(json_data, status_code=200):
    resp = MagicMock()
    resp.ok = (200 <= status_code < 300)
    resp.status_code = status_code
    resp.reason = 'OK' if resp.ok else 'Bad'
    resp.json.return_value = json_data
    return resp


def _bad_json_response(status_code=200):
    resp = MagicMock()
    resp.ok = True
    resp.status_code = status_code
    resp.reason = 'OK'
    resp.json.side_effect = ValueError('not json')
    return resp


class TestPromopClientFetch:
    def test_returns_dict_on_200(self):
        client = PromopClient(base_url='https://promop.example.com/', token='tk')
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_ok_response({'person_id': 9001, 'disease': 'MM'})) as mock_get:
            result = client.fetch_patient(9001)
        assert result == {'person_id': 9001, 'disease': 'MM'}
        # Trailing slash on base_url should be stripped.
        call_args = mock_get.call_args
        assert call_args.args[0] == 'https://promop.example.com/api/v1/patient-records/9001/'
        # Bearer auth attached.
        assert call_args.kwargs['headers']['Authorization'] == 'Bearer tk'
        assert call_args.kwargs['headers']['Accept'] == 'application/json'

    def test_returns_none_when_base_unset(self):
        client = PromopClient(base_url='', token='tk')
        with patch('trials.services.patient_info.promop_client.requests.get') as mock_get:
            assert client.fetch_patient(9001) is None
        # Network must not be touched when not configured.
        mock_get.assert_not_called()

    def test_returns_none_on_network_error(self):
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        with patch('trials.services.patient_info.promop_client.requests.get',
                   side_effect=requests.ConnectionError('boom')):
            assert client.fetch_patient(9001) is None

    def test_returns_none_on_timeout(self):
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        with patch('trials.services.patient_info.promop_client.requests.get',
                   side_effect=requests.Timeout('slow')):
            assert client.fetch_patient(9001) is None

    @pytest.mark.parametrize('status', [400, 401, 403, 404, 500, 503])
    def test_returns_none_on_non_ok_status(self, status):
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_ok_response({}, status_code=status)):
            assert client.fetch_patient(9001) is None

    def test_returns_none_on_malformed_json(self):
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_bad_json_response()):
            assert client.fetch_patient(9001) is None

    def test_returns_none_on_non_dict_body(self):
        """Lists, arrays, primitives — anything non-dict is not what the adapter expects."""
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_ok_response([{'foo': 'bar'}])):
            assert client.fetch_patient(9001) is None

    def test_no_auth_header_when_token_unset(self):
        client = PromopClient(base_url='https://promop.example.com', token='')
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_ok_response({'person_id': 9001})) as mock_get:
            client.fetch_patient(9001)
        assert 'Authorization' not in mock_get.call_args.kwargs['headers']

    @pytest.mark.parametrize('bad_id', [
        '../evil-endpoint',     # path traversal — would leak Bearer token
        'abc',                  # non-numeric
        '1; DROP TABLE persons',
        '1/../../admin',
        '',                     # empty
        None,                   # null
        0,                      # non-positive integer
        -1,
        float('inf'),           # int(inf) raises OverflowError, not ValueError
        float('nan'),           # int(nan) raises ValueError
    ])
    def test_rejects_non_positive_integer_person_id(self, bad_id):
        """URL path injection / Bearer token leakage guard (#102 self-review):
        any person_id that isn't a positive integer is rejected without a
        network call. Important because we'd otherwise interpolate the raw
        value into the URL path and the static service token in the
        Authorization header would leak to whatever path the attacker
        crafted.
        """
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        with patch('trials.services.patient_info.promop_client.requests.get') as mock_get:
            result = client.fetch_patient(bad_id)
        assert result is None
        mock_get.assert_not_called()

    def test_accepts_string_form_of_integer(self):
        """Query-param ids arrive as strings; the validator must accept them."""
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        with patch(
            'trials.services.patient_info.promop_client.requests.get',
            return_value=_ok_response({'person_id': 9001}),
        ) as mock_get:
            assert client.fetch_patient('9001') == {'person_id': 9001}
        # URL has the validated integer, not the raw string.
        assert mock_get.call_args.args[0] == 'https://promop.example.com/api/v1/patient-records/9001/'

    def test_unwraps_patient_info_envelope(self):
        """The HTTP endpoint wraps the row in `{"patient_info": {...}}`; the
        adapter expects a flat row. `fetch_patient` must unwrap so real fields
        aren't nested one level too deep and silently dropped (#144).
        """
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        envelope = {'patient_info': {'person_id': 9005, 'disease': 'breast cancer',
                                     'patient_age': 51, 'gender': 'F'}}
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_ok_response(envelope)):
            result = client.fetch_patient(9005)
        assert result == {'person_id': 9005, 'disease': 'breast cancer',
                          'patient_age': 51, 'gender': 'F'}

    def test_flat_row_passes_through_unchanged(self):
        """Invariant: an already-flat row (the psql management-command shape)
        must pass through untouched — no double-unwrap, no mangling (#144).
        """
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        flat = {'person_id': 9005, 'disease': 'breast cancer',
                'patient_age': 51, 'gender': 'F'}
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_ok_response(flat)):
            result = client.fetch_patient(9005)
        assert result == flat

    def test_envelope_with_sibling_keys_is_unwrapped(self):
        """An envelope that carries metadata alongside the row (status,
        pagination, request id) must STILL unwrap to the inner row — matching
        the inline contract, which reads `patient_info` and ignores siblings.
        Requiring a sole key would silently revert to the all-defaults bug if
        PROMOP ever adds envelope metadata (#144).
        """
        client = PromopClient(base_url='https://promop.example.com', token='tk')
        body = {'patient_info': {'person_id': 9005, 'disease': 'breast cancer'},
                'status': 'ok'}
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_ok_response(body)):
            result = client.fetch_patient(9005)
        assert result == {'person_id': 9005, 'disease': 'breast cancer'}

    def test_uses_django_settings_when_no_explicit_args(self, settings):
        """Constructor falls back to PROMOP_BASE / PROMOP_SERVICE_TOKEN settings."""
        settings.PROMOP_BASE = 'https://settings.example.com'
        settings.PROMOP_SERVICE_TOKEN = 'settingstoken'
        client = PromopClient()
        with patch('trials.services.patient_info.promop_client.requests.get',
                   return_value=_ok_response({'person_id': 9001})) as mock_get:
            client.fetch_patient(9001)
        call_args = mock_get.call_args
        assert call_args.args[0].startswith('https://settings.example.com/')
        assert call_args.kwargs['headers']['Authorization'] == 'Bearer settingstoken'


# ── #237: v1 + OAuth2 client_credentials ─────────────────────────────
from trials.services.patient_info.promop_client import _clear_token_cache

_GET = 'trials.services.patient_info.promop_client.requests.get'
_POST = 'trials.services.patient_info.promop_client.requests.post'


@pytest.fixture(autouse=True)
def _clear_service_tokens():
    _clear_token_cache()
    yield
    _clear_token_cache()


def _token_response(access_token='svc-tok', expires_in=3600, status_code=200):
    resp = MagicMock()
    resp.ok = 200 <= status_code < 300
    resp.status_code = status_code
    resp.reason = 'OK' if resp.ok else 'Bad'
    resp.json.return_value = {'access_token': access_token, 'expires_in': expires_in,
                              'token_type': 'Bearer'}
    return resp


def _oauth_client():
    return PromopClient(base_url='https://promop.example.com',
                        oauth_client_id='cid', oauth_client_secret='sec')


class TestOAuthV1:
    def test_oauth_mode_hits_v1_with_minted_bearer(self):
        client = _oauth_client()
        with patch(_POST, return_value=_token_response('svc-tok')) as mpost, \
             patch(_GET, return_value=_ok_response({'patient_info': {'person_id': 9001}})) as mget:
            result = client.fetch_patient(9001)
        assert result == {'person_id': 9001}
        assert mget.call_args.args[0] == 'https://promop.example.com/api/v1/patient-records/9001/'
        assert mget.call_args.kwargs['headers']['Authorization'] == 'Bearer svc-tok'
        # token minted via client_credentials + HTTP Basic against /o/token/
        assert mpost.call_args.args[0] == 'https://promop.example.com/o/token/'
        assert mpost.call_args.kwargs['data']['grant_type'] == 'client_credentials'
        assert mpost.call_args.kwargs['data']['scope'] == 'patient/*.read'
        assert mpost.call_args.kwargs['auth'] == ('cid', 'sec')

    def test_token_is_cached_across_calls(self):
        client = _oauth_client()
        with patch(_POST, return_value=_token_response()) as mpost, \
             patch(_GET, return_value=_ok_response({'patient_info': {'person_id': 1}})):
            client.fetch_patient(9001)
            client.fetch_patient(9002)
        assert mpost.call_count == 1  # token reused, not re-minted per fetch

    def test_oauth_token_non_ok_returns_none_no_patient_call(self):
        client = _oauth_client()
        with patch(_POST, return_value=_token_response(status_code=401)), patch(_GET) as mget:
            assert client.fetch_patient(9001) is None
        mget.assert_not_called()

    def test_oauth_token_network_error_returns_none(self):
        client = _oauth_client()
        with patch(_POST, side_effect=requests.ConnectionError('boom')), patch(_GET) as mget:
            assert client.fetch_patient(9001) is None
        mget.assert_not_called()

    def test_v1_envelope_with_user_sibling_unwraps(self):
        client = _oauth_client()
        body = {'patient_info': {'person_id': 9005, 'disease': 'MM'}, 'user': {'id': 'u1'}}
        with patch(_POST, return_value=_token_response()), patch(_GET, return_value=_ok_response(body)):
            assert client.fetch_patient(9005) == {'person_id': 9005, 'disease': 'MM'}

    def test_static_token_mode_still_uses_the_v1_url(self):
        """No OAuth config must not mean the legacy prefix (#387): promop routes
        both prefixes to one viewset, so the static service token authenticates
        against v1 too. Only the credential differs, never the URL."""
        client = PromopClient(base_url='https://promop.example.com', token='static')
        assert client.use_oauth is False
        with patch(_POST) as mpost, patch(_GET, return_value=_ok_response({'person_id': 1})) as mget:
            client.fetch_patient(9001)
        mpost.assert_not_called()
        assert mget.call_args.args[0] == 'https://promop.example.com/api/v1/patient-records/9001/'
        assert mget.call_args.kwargs['headers']['Authorization'] == 'Bearer static'

    def test_token_url_defaults_to_base_o_token(self):
        client = PromopClient(base_url='https://p.example.com',
                              oauth_client_id='c', oauth_client_secret='s')
        assert client.oauth_token_url == 'https://p.example.com/o/token/'

    def test_oauth_failure_never_falls_back_to_legacy_token(self):
        """Adversarial fail-closed: a client configured with BOTH OAuth creds
        and a legacy static token must NOT send the static token when the OAuth
        token can't be minted. OAuth mode is strict — token failure ⇒ None, no
        patient request, and the legacy Bearer is never exposed on the v1 path.
        """
        client = PromopClient(base_url='https://promop.example.com', token='legacy-static',
                              oauth_client_id='cid', oauth_client_secret='sec')
        assert client.use_oauth is True
        with patch(_POST, return_value=_token_response(status_code=503)), patch(_GET) as mget:
            assert client.fetch_patient(9001) is None
        mget.assert_not_called()  # no request at all — legacy token never used

    def test_token_response_missing_access_token_returns_none(self):
        client = _oauth_client()
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.reason = 'OK'
        resp.json.return_value = {'expires_in': 3600, 'token_type': 'Bearer'}  # no access_token
        with patch(_POST, return_value=resp), patch(_GET) as mget:
            assert client.fetch_patient(9001) is None
        mget.assert_not_called()

    def test_malformed_expires_in_falls_back_to_default_ttl(self):
        """A non-numeric expires_in must not crash token minting; the token is
        still used (default TTL) so the fetch proceeds."""
        client = _oauth_client()
        with patch(_POST, return_value=_token_response('t', expires_in='not-a-number')), \
             patch(_GET, return_value=_ok_response({'patient_info': {'person_id': 1}})) as mget:
            assert client.fetch_patient(9001) == {'person_id': 1}
        assert mget.call_args.kwargs['headers']['Authorization'] == 'Bearer t'

    def test_token_post_disallows_redirects(self):
        """The token request must not follow redirects — a redirect could bounce
        the Basic-auth client_secret to another host/scheme."""
        client = _oauth_client()
        with patch(_POST, return_value=_token_response()) as mpost, \
             patch(_GET, return_value=_ok_response({'patient_info': {'person_id': 1}})):
            client.fetch_patient(9001)
        assert mpost.call_args.kwargs['allow_redirects'] is False

    def test_partial_oauth_config_falls_back_to_the_static_token(self):
        """Only client_id (no secret) must not enable OAuth — it falls back to
        the static-token credential rather than half-authenticating. The URL
        stays on v1 either way (#387)."""
        client = PromopClient(base_url='https://promop.example.com', token='static',
                              oauth_client_id='cid')  # secret missing
        assert client.use_oauth is False
        with patch(_POST) as mpost, patch(_GET, return_value=_ok_response({'person_id': 1})) as mget:
            client.fetch_patient(9001)
        mpost.assert_not_called()
        assert mget.call_args.args[0] == 'https://promop.example.com/api/v1/patient-records/9001/'
        assert mget.call_args.kwargs['headers']['Authorization'] == 'Bearer static'

    def test_settings_drive_oauth_config(self, settings):
        settings.PROMOP_BASE = 'https://s.example.com'
        settings.PROMOP_OAUTH_CLIENT_ID = 'scid'
        settings.PROMOP_OAUTH_CLIENT_SECRET = 'ssec'
        settings.PROMOP_OAUTH_SCOPE = 'patient/*.read'
        settings.PROMOP_OAUTH_TOKEN_URL = ''
        client = PromopClient()
        assert client.use_oauth
        with patch(_POST, return_value=_token_response('t')) as mpost, \
             patch(_GET, return_value=_ok_response({'patient_info': {'person_id': 1}})) as mget:
            client.fetch_patient(1)
        assert mget.call_args.args[0] == 'https://s.example.com/api/v1/patient-records/1/'
        assert mpost.call_args.args[0] == 'https://s.example.com/o/token/'
