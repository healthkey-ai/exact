"""Tests for CachedConceptGraphClient — the release-pinned, per-source cache (#234 slice 2).

Uses a mocked ConceptGraphClient (to count/inspect fetches) over the real Django cache
(LocMemCache under test_settings). Contract: per-source reuse, release-keyed, and
fail-closed — failures and truncated sources are never cached.
"""
from unittest.mock import MagicMock

import pytest
from django.core.cache import caches

from trials.services.omop.concept_graph_client import (
    ConceptGraphClient,
    ConceptGraphResult,
    ConceptGraphUnavailable,
)
from trials.services.omop.concept_graph_cache import CachedConceptGraphClient


@pytest.fixture(autouse=True)
def _clear_cache():
    caches['default'].clear()
    yield
    caches['default'].clear()


def _res(groups, truncated=None, versions=('v1',)):
    return ConceptGraphResult(groups=groups, truncated=truncated or [],
                              versions=frozenset(versions))


def _mock_client():
    return MagicMock(spec=ConceptGraphClient)


class TestCachedExpand:
    def test_miss_then_hit(self):
        client = _mock_client()
        client.expand.return_value = _res({1: [10, 11]})
        cached = CachedConceptGraphClient(client=client)

        r1 = cached.descendants([1], release='rel-2024-12')
        r2 = cached.descendants([1], release='rel-2024-12')

        assert r1.groups == {1: [10, 11]} == r2.groups
        assert r1.versions == frozenset({'v1'})
        client.expand.assert_called_once()  # second call served from cache

    def test_per_source_reuse_across_requests(self):
        client = _mock_client()
        client.expand.side_effect = [
            _res({1: [10], 2: [20]}),   # first request fetches 1 and 2
            _res({3: [30]}),            # second request fetches only the miss (3)
        ]
        cached = CachedConceptGraphClient(client=client)

        cached.descendants([1, 2], release='r')
        r2 = cached.descendants([2, 3], release='r')

        assert r2.groups == {2: [20], 3: [30]}   # 2 from cache, 3 fresh
        assert client.expand.call_count == 2
        second_missing = client.expand.call_args_list[1].args[0]
        assert second_missing == [3]             # only the uncached id was fetched

    def test_truncated_source_not_cached(self):
        client = _mock_client()
        client.expand.return_value = _res({1: [10]}, truncated=[1])
        cached = CachedConceptGraphClient(client=client)

        r1 = cached.descendants([1], release='r')
        r2 = cached.descendants([1], release='r')

        assert r1.truncated == [1]
        assert client.expand.call_count == 2     # not cached → re-fetched

    def test_mixed_batch_caches_only_clean_sources(self):
        # Partial-projection safety: in a batch where one source truncates and
        # another is clean, only the clean one is cached; a later request serves
        # the clean one from cache and re-fetches the truncated one.
        client = _mock_client()
        client.expand.side_effect = [
            _res({1: [10], 2: [20]}, truncated=[1]),  # 1 truncated, 2 clean
            _res({1: [10]}, truncated=[1]),           # re-fetch of the uncached 1
        ]
        cached = CachedConceptGraphClient(client=client)

        r1 = cached.descendants([1, 2], release='r')
        assert r1.groups == {1: [10], 2: [20]}
        assert r1.truncated == [1]

        r2 = cached.descendants([1, 2], release='r')
        assert r2.groups == {1: [10], 2: [20]}
        assert client.expand.call_count == 2
        # Only the truncated id was re-fetched; the clean one came from cache.
        assert client.expand.call_args_list[1].args[0] == [1]

    def test_failure_not_cached_and_propagates(self):
        client = _mock_client()
        client.expand.side_effect = ConceptGraphUnavailable('promop down')
        cached = CachedConceptGraphClient(client=client)

        with pytest.raises(ConceptGraphUnavailable):
            cached.descendants([1], release='r')

        # A later successful call must re-invoke the client (nothing was cached).
        client.expand.side_effect = None
        client.expand.return_value = _res({1: [10]})
        assert cached.descendants([1], release='r').groups == {1: [10]}
        assert client.expand.call_count == 2

    def test_release_required(self):
        cached = CachedConceptGraphClient(client=_mock_client())
        with pytest.raises(ValueError):
            cached.descendants([1], release='')

    def test_bad_direction_raises(self):
        cached = CachedConceptGraphClient(client=_mock_client())
        with pytest.raises(ValueError):
            cached.expand([1], 'sideways', release='r')

    def test_empty_ids_no_client_call(self):
        client = _mock_client()
        cached = CachedConceptGraphClient(client=client)
        res = cached.descendants([], release='r')
        assert res.groups == {}
        client.expand.assert_not_called()

    def test_release_scopes_the_key(self):
        client = _mock_client()
        client.expand.side_effect = [_res({1: [10]}), _res({1: [99]})]
        cached = CachedConceptGraphClient(client=client)

        a = cached.descendants([1], release='rel-A')
        b = cached.descendants([1], release='rel-B')   # different release → separate key

        assert a.groups == {1: [10]}
        assert b.groups == {1: [99]}
        assert client.expand.call_count == 2

    def test_relationship_ids_scope_the_key(self):
        client = _mock_client()
        client.expand.side_effect = [_res({1: [10]}), _res({1: [20]})]
        cached = CachedConceptGraphClient(client=client)

        a = cached.descendants([1], release='r', relationship_ids=['Has targeted therapy'])
        b = cached.descendants([1], release='r', relationship_ids=['Has cytotoxic chemo'])

        assert a.groups == {1: [10]}
        assert b.groups == {1: [20]}
        assert client.expand.call_count == 2

    def test_assembles_cached_and_fresh(self):
        client = _mock_client()
        client.expand.side_effect = [_res({1: [10]}), _res({2: [20]}, versions=('v2',))]
        cached = CachedConceptGraphClient(client=client)

        cached.descendants([1], release='r')             # seed cache for 1
        r = cached.descendants([1, 2], release='r')      # 1 cached, 2 fresh

        assert r.groups == {1: [10], 2: [20]}
        assert r.versions == frozenset({'v1', 'v2'})
        second_missing = client.expand.call_args_list[1].args[0]
        assert second_missing == [2]

    def test_direction_scopes_the_key(self):
        # A descendants/ancestors collision would be catastrophic — pin it.
        client = _mock_client()
        client.expand.side_effect = [_res({1: [10]}), _res({1: [20]})]
        cached = CachedConceptGraphClient(client=client)
        d = cached.descendants([1], release='r')
        a = cached.ancestors([1], release='r')
        assert d.groups == {1: [10]}
        assert a.groups == {1: [20]}
        assert client.expand.call_count == 2

    def test_vocabulary_ids_scope_the_key(self):
        client = _mock_client()
        client.expand.side_effect = [_res({1: [10]}), _res({1: [20]})]
        cached = CachedConceptGraphClient(client=client)
        cached.descendants([1], release='r', vocabulary_ids=['HemOnc'])
        cached.descendants([1], release='r', vocabulary_ids=['RxNorm'])
        assert client.expand.call_count == 2

    def test_concept_class_ids_scope_the_key(self):
        client = _mock_client()
        client.expand.side_effect = [_res({1: [10]}), _res({1: [20]})]
        cached = CachedConceptGraphClient(client=client)
        cached.descendants([1], release='r', concept_class_ids=['Ingredient'])
        cached.descendants([1], release='r', concept_class_ids=['Drug Class'])
        assert client.expand.call_count == 2

    def test_max_levels_scope_the_key(self):
        client = _mock_client()
        client.expand.side_effect = [_res({1: [10]}), _res({1: [20]})]
        cached = CachedConceptGraphClient(client=client)
        cached.ancestors([1], release='r', max_levels=1)
        cached.ancestors([1], release='r', max_levels=2)
        assert client.expand.call_count == 2

    def test_empty_group_is_cached(self):
        # A legitimately-empty expansion (leaf, not truncated, not raised) is a
        # KNOWN-empty result and IS cacheable — only Unavailable/truncated must not cache.
        client = _mock_client()
        client.expand.return_value = _res({1: []})
        cached = CachedConceptGraphClient(client=client)
        r1 = cached.descendants([1], release='r')
        r2 = cached.descendants([1], release='r')
        assert r1.groups == {1: []} == r2.groups
        client.expand.assert_called_once()  # empty result served from cache

    def test_cache_read_error_degrades_to_miss(self):
        # A broken cache backend (e.g. missing DatabaseCache table) must not raise —
        # degrade to a miss and call the client.
        client = _mock_client()
        client.expand.return_value = _res({1: [10]})
        cached = CachedConceptGraphClient(client=client)
        cached.cache = MagicMock()
        cached.cache.get.side_effect = Exception('cache backend down')
        r = cached.descendants([1], release='r')
        assert r.groups == {1: [10]}
        client.expand.assert_called_once()

    def test_cache_write_error_is_swallowed(self):
        client = _mock_client()
        client.expand.return_value = _res({1: [10]})
        cached = CachedConceptGraphClient(client=client)
        cached.cache = MagicMock()
        cached.cache.get.return_value = None
        cached.cache.set.side_effect = Exception('cache backend down')
        r = cached.descendants([1], release='r')   # must not raise on the failed set
        assert r.groups == {1: [10]}


@pytest.mark.django_db
def test_cache_works_over_a_database_cache_backend(settings):
    """Exercise the real production backend — a DatabaseCache (pickle round-trip via
    the DB), not just LocMem — using the client's cache_alias param to target it."""
    from django.core.management import call_command
    settings.CACHES = {
        **settings.CACHES,
        'cg_db': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': 'test_concept_graph_cache',
        },
    }
    call_command('createcachetable', 'test_concept_graph_cache')

    client = _mock_client()
    client.expand.return_value = _res({1: [10, 11]})
    cached = CachedConceptGraphClient(client=client, cache_alias='cg_db')

    r1 = cached.descendants([1], release='r')
    r2 = cached.descendants([1], release='r')
    assert r1.groups == {1: [10, 11]} == r2.groups
    client.expand.assert_called_once()  # second call served from the DB-backed cache
