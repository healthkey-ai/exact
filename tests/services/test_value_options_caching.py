"""Regression guard for the `all_options()` cache + build cost (perf #match-endpoint).

Building `all_options()` is query-heavy (per-disease therapy and marker enums).
It is cached for 1h so only the first call pays that cost; every subsequent
call must be a pure cache hit with zero queries. A refactor that broke the
cache key, dropped the `cache.set`, or made the result non-cacheable would
silently restore the ~6s-per-request regression — this test catches that at the
unit level (independent of the cache backend).

It also guards the cold-build query count: `Therapy.full_title()` once issued a
per-therapy `components` query (an N+1 that made the cold build ~488 queries /
~1.3s). The fix (sort components in Python so a prefetch cache is reused) cut it
to ~204. A budget test keeps a future change from silently reintroducing the
N+1.
"""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from trials.models import Therapy
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


@pytest.mark.django_db
def test_cold_build_stays_under_query_budget():
    # Pre-fix the cold build was ~488 queries (a per-therapy components N+1).
    # Budget is generous headroom over the ~204 post-fix count so legitimate
    # seed-data growth won't flake, but a reintroduced N+1 (which scales with
    # the ~166 therapies) blows straight past it.
    cache.clear()
    with CaptureQueriesContext(connection) as ctx:
        ValueOptions().get_all_options()
    assert len(ctx.captured_queries) < 300, (
        f'all_options() cold build issued {len(ctx.captured_queries)} queries — '
        'a components N+1 (Therapy.full_title) may have regressed.'
    )


@pytest.mark.django_db
def test_full_title_reuses_prefetched_components():
    # The precise N+1 guard: with components prefetched, full_title() must not
    # issue any further queries. `.order_by()` inside full_title would bust the
    # prefetch cache and fire one query per therapy.
    therapies = list(Therapy.objects.prefetch_related('components').all())
    assert therapies, 'seed data must include therapies'
    with CaptureQueriesContext(connection) as ctx:
        for therapy in therapies:
            therapy.full_title()
    assert len(ctx.captured_queries) == 0, (
        f'full_title() issued {len(ctx.captured_queries)} queries despite a '
        'prefetched components cache — the N+1 has regressed.'
    )
