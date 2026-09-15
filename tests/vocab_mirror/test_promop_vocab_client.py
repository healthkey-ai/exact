"""PromopVocabClient request-contract tests (#250).

Guards the request the client makes to promop's snapshot endpoint — in particular
the Accept header, which must offer ``*/*`` alongside ``application/x-ndjson`` so
promop's DRF content negotiation cannot 406 a stream the client parses itself
(promop's VocabSnapshotView streams ndjson without registering a matching
renderer). Found by the local vocab-mirror e2e.
"""
import json

import pytest

from vocab_mirror import promop_vocab_client as pvc
from vocab_mirror.promop_vocab_client import PromopVocabClient


class _FakeStreamResp:
    def __init__(self, lines, status_code=200, headers=None):
        self._lines = lines
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.reason = 'OK' if self.ok else 'Conflict'
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def iter_lines(self, decode_unicode=False):
        yield from self._lines


def _client():
    c = PromopVocabClient(
        base_url='http://promop', oauth_client_id='cid', oauth_client_secret='sec',
        oauth_token_url='http://promop/o/token/')
    c._authorization = lambda: 'Bearer t'  # skip the real OAuth mint
    return c


def test_stream_snapshot_accept_offers_ndjson_and_wildcard(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, stream=None, timeout=None):
        captured['url'] = url
        captured['headers'] = headers
        return _FakeStreamResp([
            json.dumps({'concept_id': 1}),
            json.dumps({'__done': True, 'rows': 1}),
        ])

    monkeypatch.setattr(pvc.requests, 'get', fake_get)

    rows = list(_client().stream_snapshot(2, 'concept'))

    assert rows == [{'concept_id': 1}, {'__done': True, 'rows': 1}]
    assert captured['url'] == 'http://promop/api/v1/vocab-releases/2/snapshot/concept/'
    accept = captured['headers']['Accept']
    # ndjson preference is kept (listed first), but */* must be offered so a server
    # that streams ndjson without an ndjson renderer cannot 406 the request.
    assert accept.startswith('application/x-ndjson')
    assert '*/*' in accept
    assert captured['headers']['Authorization'] == 'Bearer t'


def _run(monkeypatch, resp):
    monkeypatch.setattr(pvc.requests, 'get', lambda *a, **k: resp)
    return list(_client().stream_snapshot(2, 'concept'))


def test_stream_snapshot_409_raises_release_superseded(monkeypatch):
    # promop snapshots are latest-only (#373): a 409 means our release is stale →
    # a re-resolve signal, NOT a hard failure.
    resp = _FakeStreamResp([], status_code=409)
    with pytest.raises(pvc.VocabReleaseSuperseded):
        _run(monkeypatch, resp)


def test_stream_snapshot_verifies_release_id_header(monkeypatch):
    # A matching X-Vocab-Release-Id passes; a mismatch fails closed.
    ok = _FakeStreamResp(
        [json.dumps({'concept_id': 1}), json.dumps({'__done': True, 'rows': 1})],
        headers={'X-Vocab-Release-Id': '2'})
    assert _run(monkeypatch, ok) == [{'concept_id': 1}, {'__done': True, 'rows': 1}]

    wrong = _FakeStreamResp([json.dumps({'concept_id': 1})],
                            headers={'X-Vocab-Release-Id': '99'})
    with pytest.raises(pvc.VocabSyncError):
        _run(monkeypatch, wrong)


def test_stream_snapshot_tolerates_absent_release_id_header(monkeypatch):
    # An older promop without the header still works (verify only when present).
    resp = _FakeStreamResp([json.dumps({'__done': True, 'rows': 0})], headers={})
    assert _run(monkeypatch, resp) == [{'__done': True, 'rows': 0}]


class TestVocabCredential:
    """The vocab client has its own credential *settings* but should carry the
    same named token as the patient client (#448) — one logical service must not
    appear in promop's audit as two principals. Its rules match the patient
    client's: fail closed locally on a broken credential rather than posting half
    of one, and never ask for more than reads.
    """

    @pytest.mark.parametrize('kwargs, expected', [
        ({'oauth_client_id': 'cid', 'oauth_client_secret': ''}, 'client_secret'),
        ({'oauth_client_id': '', 'oauth_client_secret': 'sec'}, 'client_id'),
        ({'oauth_client_id': '', 'oauth_client_secret': ''}, 'no vocab credential'),
    ])
    def test_incomplete_credential_raises_without_a_token_request(
            self, monkeypatch, kwargs, expected):
        posted = []
        monkeypatch.setattr(pvc.requests, 'post',
                            lambda *a, **k: posted.append(a) or None)
        client = PromopVocabClient(base_url='http://promop', token='',
                                   oauth_token_url='http://promop/o/token/', **kwargs)
        with pytest.raises(pvc.VocabSyncError) as exc:
            client._authorization()
        assert expected in str(exc.value)
        assert posted == []

    @pytest.mark.parametrize('kwargs', [
        {'oauth_client_id': 'cid'},      # secret missing
        {'oauth_client_secret': 'sec'},  # id missing
    ])
    def test_half_an_oauth_pair_does_not_fall_back_to_the_static_token(
            self, monkeypatch, kwargs):
        """Parity with the patient client (#448): a dropped OAuth secret must
        not silently substitute the other credential — that is the
        shared-credential behaviour the migration removes."""
        posted = []
        monkeypatch.setattr(pvc.requests, 'post',
                            lambda *a, **k: posted.append(a) or None)
        client = PromopVocabClient(base_url='http://promop', token='static-tok',
                                   oauth_token_url='http://promop/o/token/', **kwargs)
        with pytest.raises(pvc.VocabSyncError):
            client._authorization()
        assert posted == []

    def test_static_token_authorizes_without_minting(self, monkeypatch):
        """The named PRomop service token is a plain bearer: no OAuth round-trip,
        and it is what makes the request authenticate as `urn:service|exact`."""
        posted = []
        monkeypatch.setattr(pvc.requests, 'post',
                            lambda *a, **k: posted.append(a) or None)
        client = PromopVocabClient(base_url='http://promop', token='named-tok')
        assert client.use_oauth is False
        assert client.oauth_config_incomplete is False
        assert client._authorization() == 'Bearer named-tok'
        assert posted == []

    def test_oauth_wins_when_both_credentials_are_configured(self, monkeypatch):
        """Same precedence as the patient client, so a deployment can't be
        configured one way and behave the other."""
        from trials.services.patient_info.promop_client import _clear_token_cache

        _clear_token_cache()

        class _TokenResp:
            ok = True
            status_code = 200
            reason = 'OK'

            @staticmethod
            def json():
                return {'access_token': 'minted', 'expires_in': 3600}

        monkeypatch.setattr(
            'trials.services.patient_info.promop_client.requests.post',
            lambda *a, **k: _TokenResp())
        client = PromopVocabClient(base_url='http://promop', token='static-tok',
                                   oauth_client_id='cid', oauth_client_secret='sec',
                                   oauth_scope='system/*.read',
                                   oauth_token_url='http://promop/o/token/')
        try:
            assert client._authorization() == 'Bearer minted'
        finally:
            _clear_token_cache()

    def test_settings_supply_the_static_token(self, settings):
        settings.PROMOP_VOCAB_BASE = 'http://promop'
        settings.PROMOP_VOCAB_SERVICE_TOKEN = 'from-settings'
        settings.PROMOP_VOCAB_OAUTH_CLIENT_ID = ''
        settings.PROMOP_VOCAB_OAUTH_CLIENT_SECRET = ''
        assert PromopVocabClient()._authorization() == 'Bearer from-settings'

    def test_token_request_uses_the_vocab_scope(self, monkeypatch):
        """On the OAuth path the scope reaching promop is the configured one.
        `system/*.read` is what promop's VocabReadPermission accepts *in addition
        to* `patient/*.read`, so this is not a second grant EXACT requires — it
        is what an OAuth client would need if it ran on that path instead of the
        named token. Either way, no write."""
        from trials.services.patient_info.promop_client import _clear_token_cache

        _clear_token_cache()
        captured = {}

        class _TokenResp:
            ok = True
            status_code = 200
            reason = 'OK'

            @staticmethod
            def json():
                return {'access_token': 'vt', 'expires_in': 3600}

        def fake_post(url, data=None, auth=None, timeout=None, allow_redirects=None):
            captured.update(url=url, data=data, auth=auth)
            return _TokenResp()

        monkeypatch.setattr(
            'trials.services.patient_info.promop_client.requests.post', fake_post)
        client = PromopVocabClient(base_url='http://promop', oauth_client_id='cid',
                                   oauth_client_secret='sec',
                                   oauth_scope='system/*.read',
                                   oauth_token_url='http://promop/o/token/')
        try:
            assert client._authorization() == 'Bearer vt'
        finally:
            _clear_token_cache()
        assert captured['url'] == 'http://promop/o/token/'
        assert captured['data']['scope'] == 'system/*.read'
        assert 'write' not in captured['data']['scope']
        assert captured['auth'] == ('cid', 'sec')

    def test_scope_falls_back_to_the_vocab_read_scope_when_unconfigured(self, settings):
        """Deleted rather than assigned — assigning would assert the test's own
        value. The fallback is a read scope; write never appears."""
        del settings.PROMOP_VOCAB_OAUTH_SCOPE
        client = PromopVocabClient(base_url='http://promop')
        assert client.oauth_scope == 'system/*.read'
        assert 'write' not in client.oauth_scope

    @pytest.mark.parametrize('call', ['latest', 'snapshot'])
    def test_requests_assert_no_actor_and_carry_no_body(self, monkeypatch, call):
        """`_headers(extra=...)` takes arbitrary extra headers (If-None-Match
        uses it) — that is the seam an actor or provenance header would enter
        through. promop rejects unsigned actor claims from a service credential
        (#448), and this client has no business making one."""
        captured = {}

        class _LatestResp(_FakeStreamResp):
            headers = {'ETag': '"e"'}

            @staticmethod
            def json():
                return {'release_id': 2}

        def fake_get(url, headers=None, **kwargs):
            captured.update(url=url, headers=headers, kwargs=kwargs)
            if call == 'latest':
                return _LatestResp([])
            return _FakeStreamResp([json.dumps({'__done': True, 'rows': 0})])

        monkeypatch.setattr(pvc.requests, 'get', fake_get)
        client = _client()
        if call == 'latest':
            client.get_latest_release(if_none_match='"etag"')
        else:
            list(client.stream_snapshot(2, 'concept'))

        assert captured, 'no request was made — this assertion would prove nothing'
        blob = ' '.join(f'{k}:{v}' for k, v in captured['headers'].items()).lower()
        for field in ('actor_iss', 'actor_sub', 'provenance', 'on-behalf-of'):
            assert field not in blob
        assert captured['kwargs'].get('json') is None
        assert captured['kwargs'].get('data') is None
        assert not captured['kwargs'].get('params')
        assert '?' not in captured['url']

    @pytest.mark.parametrize('call', ['latest', 'snapshot'])
    def test_no_credential_means_no_request_through_the_public_api(
            self, monkeypatch, call):
        """The other tests drive `_authorization()` directly, which pins the
        decision but not the consequence. What matters is that nothing reaches
        the network uncredentialed — so exercise the two public methods and
        watch `requests.get`, the way the patient client's tests do."""
        called = []
        monkeypatch.setattr(pvc.requests, 'get',
                            lambda *a, **k: called.append(a) or None)
        client = PromopVocabClient(base_url='http://promop', token='',
                                   oauth_client_id='', oauth_client_secret='')
        with pytest.raises(pvc.VocabSyncError):
            if call == 'latest':
                client.get_latest_release()
            else:
                list(client.stream_snapshot(2, 'concept'))
        assert called == []

