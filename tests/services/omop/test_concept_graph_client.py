"""Tests for ConceptGraphClient — the thin API layer over promop's concept graph (#234).

Contract under test: `expand`/`descendants`/`ancestors` return a `ConceptGraphResult`
(groups de-duped + sorted, truncation surfaced, versions collected) on success, and
**raise** `ConceptGraphUnavailable` on any hard failure — unlike PromopClient, which
returns None. Raising is deliberate: the graph feeds a persisted eligibility projection,
so a silent empty expansion would fail open.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from trials.services.omop.concept_graph_client import (
    ConceptGraphClient,
    ConceptGraphResult,
    ConceptGraphUnavailable,
    GRAPH_MAX_SOURCE_IDS,
)

PATCH = 'trials.services.omop.concept_graph_client.requests.get'


def _resp(json_data, status=200):
    r = MagicMock()
    r.ok = 200 <= status < 300
    r.status_code = status
    r.reason = 'OK' if r.ok else 'Bad'
    r.json.return_value = json_data
    return r


def _bad_json(status=200):
    r = MagicMock()
    r.ok = True
    r.status_code = status
    r.reason = 'OK'
    r.json.side_effect = ValueError('not json')
    return r


def _client():
    return ConceptGraphClient(base_url='https://promop.example.com/', token='tk')


class TestExpand:
    def test_descendants_happy(self):
        body = {
            'direction': 'descendants',
            'results': {'9901001': [
                {'concept_id': 9901002, 'vocabulary_version': 'HemOnc 2024-12-19'},
                {'concept_id': 9901003, 'vocabulary_version': 'HemOnc 2024-12-19'},
            ]},
            'truncated': [],
        }
        with patch(PATCH, return_value=_resp(body)) as m:
            res = _client().descendants([9901001], relationship_ids=['Has targeted therapy'])

        assert isinstance(res, ConceptGraphResult)
        assert res.groups == {9901001: [9901002, 9901003]}
        assert res.truncated == []
        assert res.versions == frozenset({'HemOnc 2024-12-19'})

        # URL + repeatable query params + Bearer auth.
        assert m.call_args.args[0] == 'https://promop.example.com/api/v1/concepts/graph/'
        params = m.call_args.kwargs['params']
        assert ('direction', 'descendants') in params
        assert ('concept_id', 9901001) in params
        assert ('relationship_id', 'Has targeted therapy') in params
        assert m.call_args.kwargs['headers']['Authorization'] == 'Bearer tk'
        assert m.call_args.kwargs['headers']['Accept'] == 'application/json'

    def test_groups_deduped_and_sorted(self):
        body = {'results': {'1': [
            {'concept_id': 30}, {'concept_id': 10}, {'concept_id': 30}, {'concept_id': 20},
        ]}, 'truncated': []}
        with patch(PATCH, return_value=_resp(body)):
            res = _client().descendants([1])
        assert res.groups == {1: [10, 20, 30]}

    def test_truncation_surfaced(self):
        body = {'results': {'1': [{'concept_id': 2}]}, 'truncated': [1]}
        with patch(PATCH, return_value=_resp(body)):
            res = _client().descendants([1])
        assert res.truncated == [1]

    def test_batches_over_the_cap(self):
        ids = list(range(1, 2 * GRAPH_MAX_SOURCE_IDS + 5))  # forces 3 chunks
        with patch(PATCH, return_value=_resp({'results': {}, 'truncated': []})) as m:
            _client().descendants(ids)
        assert m.call_count == 3
        first = m.call_args_list[0].kwargs['params']
        assert sum(1 for k, _ in first if k == 'concept_id') == GRAPH_MAX_SOURCE_IDS

    def test_empty_ids_makes_no_call(self):
        with patch(PATCH) as m:
            res = _client().descendants([])
        m.assert_not_called()
        assert res.groups == {}
        assert res.truncated == []

    def test_invalid_ids_dropped_before_request(self):
        with patch(PATCH, return_value=_resp({'results': {'5': []}, 'truncated': []})) as m:
            _client().descendants(['abc', '5', -1, 0, 5])  # dupes/junk/non-positive
        cids = [v for k, v in m.call_args.kwargs['params'] if k == 'concept_id']
        assert cids == [5]

    def test_ancestors_direction(self):
        with patch(PATCH, return_value=_resp({'results': {}, 'truncated': []})) as m:
            _client().ancestors([1], max_levels=1, vocabulary_ids=['HemOnc'])
        params = m.call_args.kwargs['params']
        assert ('direction', 'ancestors') in params
        assert ('max_levels', 1) in params
        assert ('vocabulary_id', 'HemOnc') in params

    def test_bad_direction_raises_value_error(self):
        with pytest.raises(ValueError):
            _client().expand([1], 'sideways')

    def test_base_unset_raises(self):
        client = ConceptGraphClient(base_url='', token='tk')
        with patch(PATCH) as m:
            with pytest.raises(ConceptGraphUnavailable):
                client.descendants([1])
        m.assert_not_called()

    @pytest.mark.parametrize('status', [400, 401, 403, 404, 500, 503])
    def test_non_ok_status_raises(self, status):
        with patch(PATCH, return_value=_resp({}, status=status)):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_network_error_raises(self):
        with patch(PATCH, side_effect=requests.ConnectionError('boom')):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_timeout_raises(self):
        with patch(PATCH, side_effect=requests.Timeout('slow')):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_malformed_json_raises(self):
        with patch(PATCH, return_value=_bad_json()):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_non_dict_body_raises(self):
        with patch(PATCH, return_value=_resp([{'concept_id': 1}])):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_no_auth_header_when_token_unset(self):
        client = ConceptGraphClient(base_url='https://promop.example.com', token='')
        with patch(PATCH, return_value=_resp({'results': {}, 'truncated': []})) as m:
            client.descendants([1])
        assert 'Authorization' not in m.call_args.kwargs['headers']

    def test_settings_fallback_to_promop_base(self, settings):
        # PROMOP_API_BASE unset → fall back to the shared PROMOP_BASE (same host).
        settings.PROMOP_API_BASE = ''
        settings.PROMOP_BASE = 'https://fallback.example.com'
        settings.PROMOP_SERVICE_TOKEN = 'ctok'
        client = ConceptGraphClient()
        with patch(PATCH, return_value=_resp({'results': {}, 'truncated': []})) as m:
            client.descendants([1])
        assert m.call_args.args[0].startswith('https://fallback.example.com/')
        assert m.call_args.kwargs['headers']['Authorization'] == 'Bearer ctok'


class TestExpandHardening:
    """A 2xx body with the wrong shape is UNUSABLE — it must raise
    ConceptGraphUnavailable (fail-closed), not escape as a raw
    AttributeError/TypeError or read as an empty (fail-open) expansion.
    """

    def test_missing_results_key_raises(self):
        with patch(PATCH, return_value=_resp({'truncated': []})):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_results_not_dict_raises(self):
        with patch(PATCH, return_value=_resp({'results': [], 'truncated': []})):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_nodes_not_list_raises(self):
        with patch(PATCH, return_value=_resp({'results': {'1': 7}, 'truncated': []})):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_node_not_dict_raises(self):
        with patch(PATCH, return_value=_resp({'results': {'1': [7]}, 'truncated': []})):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_truncated_not_list_raises(self):
        with patch(PATCH, return_value=_resp({'results': {}, 'truncated': 1})):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants([1])

    def test_unrequested_source_id_ignored(self):
        body = {'results': {'1': [{'concept_id': 2}], '999': [{'concept_id': 3}]},
                'truncated': [999]}
        with patch(PATCH, return_value=_resp(body)):
            res = _client().descendants([1])
        assert res.groups == {1: [2]}          # only the requested id
        assert 999 not in res.groups
        assert res.truncated == []             # echoed unrequested id dropped

    def test_merges_across_chunks(self):
        ids = list(range(1, GRAPH_MAX_SOURCE_IDS + 3))  # 2 chunks: 200 + 2
        b1 = {'results': {'1': [{'concept_id': 10, 'vocabulary_version': 'v1'}]}, 'truncated': [1]}
        b2 = {'results': {str(GRAPH_MAX_SOURCE_IDS + 1): [{'concept_id': 99,
                                                           'vocabulary_version': 'v2'}]},
              'truncated': []}
        with patch(PATCH, side_effect=[_resp(b1), _resp(b2)]) as m:
            res = _client().descendants(ids)
        assert m.call_count == 2
        assert res.groups[1] == [10]
        assert res.groups[GRAPH_MAX_SOURCE_IDS + 1] == [99]
        assert res.groups[2] == []             # requested, no results → empty
        assert res.versions == frozenset({'v1', 'v2'})
        assert res.truncated == [1]

    def test_mid_batch_chunk_error_raises(self):
        ids = list(range(1, GRAPH_MAX_SOURCE_IDS + 2))  # 2 chunks
        ok = _resp({'results': {}, 'truncated': []})
        with patch(PATCH, side_effect=[ok, _resp({}, status=500)]):
            with pytest.raises(ConceptGraphUnavailable):
                _client().descendants(ids)
