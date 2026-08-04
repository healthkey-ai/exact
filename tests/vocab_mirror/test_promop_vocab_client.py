"""PromopVocabClient request-contract tests (#250).

Guards the request the client makes to promop's snapshot endpoint — in particular
the Accept header, which must offer ``*/*`` alongside ``application/x-ndjson`` so
promop's DRF content negotiation cannot 406 a stream the client parses itself
(promop's VocabSnapshotView streams ndjson without registering a matching
renderer). Found by the local vocab-mirror e2e.
"""
import json

from vocab_mirror import promop_vocab_client as pvc
from vocab_mirror.promop_vocab_client import PromopVocabClient


class _FakeStreamResp:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.reason = 'OK'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

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
