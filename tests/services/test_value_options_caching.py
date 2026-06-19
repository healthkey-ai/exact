"""Regression guard for the `all_options()` cache (perf #match-endpoint).

Building `all_options()` costs ~490 sequential DB queries (per-disease therapy
and marker enums). It is cached for 1h so only the first call pays that cost;
every subsequent call must be a pure cache hit with zero queries. A refactor
that broke the cache key, dropped the `cache.set`, or made the result
non-cacheable would silently restore the ~6s-per-request regression — this
test catches that at the unit level (independent of the cache backend).
"""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from trials.services.value_options import ValueOptions


@pytest.mark.django_db
def test_all_options_is_cached_after_first_build():
    cache.clear()

    with CaptureQueriesContext(connection) as cold:
        ValueOptions().all_options()
    # The cold build is genuinely query-heavy — proves we're measuring the
    # real reference-data assembly, not an already-warm path.
    assert len(cold.captured_queries) > 50

    # A fresh instance must reuse the cached blob: zero queries on the warm
    # hit. This is what keeps cold gunicorn workers / Cloud Run instances from
    # re-paying the build once a shared Redis cache holds the value.
    with CaptureQueriesContext(connection) as warm:
        ValueOptions().all_options()
    assert len(warm.captured_queries) == 0


@pytest.mark.django_db
def test_all_options_returns_equal_payload_warm_and_cold():
    cache.clear()
    cold = ValueOptions().all_options()
    warm = ValueOptions().all_options()
    # The cache must round-trip the full shape, not a truncated/stale blob.
    assert warm == cold
    assert set(warm.keys()) == set(cold.keys())
