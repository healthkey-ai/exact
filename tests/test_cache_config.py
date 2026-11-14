"""Regression tests for the cache backend resolution (perf #match-endpoint).

The slow `/trials/{id}/match/` (~6s on staging) traced back to
`CACHES['default']` being an unconditional per-process `LocMemCache`: under
`gunicorn --workers 4` on Cloud Run the 1h `ValueOptions.all_options()` cache
(~490 queries to build) was never shared, so cold workers re-paid the full
build. These tests pin the resolution so a refactor can't silently regress the
deploy back to a per-process cache.
"""
import re
import ssl
from pathlib import Path

import pytest

from exact.cache_config import build_caches, LOCMEM_BACKEND, REDIS_BACKEND

SETTINGS_PATH = Path(__file__).resolve().parents[1] / 'exact' / 'settings.py'


class TestBuildCaches:
    def test_no_redis_url_falls_back_to_locmem(self):
        # No Redis configured must stay on the per-process backend — pointing
        # at a non-existent Redis would break every cache.get/set.
        for empty in (None, '', '   '):
            caches = build_caches(redis_url=empty, debug=False, environment='staging')
            assert caches['default']['BACKEND'] == LOCMEM_BACKEND

    def test_local_env_stays_on_locmem_even_with_redis_url(self):
        # .env.example ships REDIS_URL=redis://localhost:6379 for Celery. A
        # local dev without a running Redis must NOT get RedisCache, or auth
        # token lookups / all_options() reads would raise ConnectionError.
        local = build_caches(
            redis_url='redis://localhost:6379', debug=False, environment='local'
        )
        assert local['default']['BACKEND'] == LOCMEM_BACKEND
        debug_caches = build_caches(
            redis_url='redis://localhost:6379', debug=True, environment='dev'
        )
        assert debug_caches['default']['BACKEND'] == LOCMEM_BACKEND

    def test_redis_url_uses_shared_redis_backend(self):
        caches = build_caches(
            redis_url='redis://cache:6379', debug=False, environment='staging'
        )
        default = caches['default']
        assert default['BACKEND'] == REDIS_BACKEND
        assert default['LOCATION'] == 'redis://cache:6379'
        # Namespaced so a shared Redis between envs can't cross-read blobs.
        assert default['KEY_PREFIX'] == 'staging'

    def test_plain_redis_has_no_ssl_options(self):
        caches = build_caches(
            redis_url='redis://cache:6379', debug=False, environment='prod'
        )
        assert 'OPTIONS' not in caches['default']

    def test_rediss_deployed_requires_cert_verification(self):
        # Mirrors the Celery TLS policy (#157): deployed envs verify the cert.
        caches = build_caches(
            redis_url='rediss://cache:6380', debug=False, environment='staging'
        )
        assert caches['default']['OPTIONS']['ssl_cert_reqs'] == ssl.CERT_REQUIRED

    def test_rediss_forwards_ca_bundle(self):
        caches = build_caches(
            redis_url='rediss://cache:6380',
            debug=False,
            environment='prod',
            redis_ca_certs='/etc/ssl/redis-ca.pem',
        )
        assert caches['default']['OPTIONS']['ssl_ca_certs'] == '/etc/ssl/redis-ca.pem'

    def test_url_querystring_cannot_downgrade_tls_verification(self):
        # redis-py's from_url() lets querystring args override OPTIONS kwargs,
        # so a deployed `?ssl_cert_reqs=none` would silently disable cert
        # verification. The TLS-policy params must be stripped from LOCATION so
        # our enforced CERT_REQUIRED wins.
        caches = build_caches(
            redis_url='rediss://cache:6380/0?ssl_cert_reqs=none&ssl_check_hostname=false&db=2',
            debug=False,
            environment='staging',
        )
        default = caches['default']
        assert 'ssl_cert_reqs' not in default['LOCATION']
        assert 'ssl_check_hostname' not in default['LOCATION']
        # Non-TLS query params are preserved.
        assert 'db=2' in default['LOCATION']
        assert default['OPTIONS']['ssl_cert_reqs'] == ssl.CERT_REQUIRED

    def test_url_querystring_strip_preserves_effective_tls_at_connection_layer(self):
        # End-to-end guard against the real redis-py precedence rule: build the
        # connection pool from the resolved LOCATION + OPTIONS and assert the
        # SSLConnection actually verifies the cert.
        redis = pytest.importorskip('redis')
        caches = build_caches(
            redis_url='rediss://cache:6380?ssl_cert_reqs=none',
            debug=False,
            environment='prod',
        )
        default = caches['default']
        pool = redis.ConnectionPool.from_url(
            default['LOCATION'], **default.get('OPTIONS', {})
        )
        assert pool.connection_kwargs.get('ssl_cert_reqs') == ssl.CERT_REQUIRED


class TestSettingsDoesNotHardcodeLocmem:
    """The original bug: a literal unconditional LocMemCache in settings.py.
    Guard at the source so the deploy can't regress to a per-process cache."""

    def test_cache_backend_is_resolved_not_hardcoded(self):
        source = SETTINGS_PATH.read_text()
        # No bare `CACHES = { ... LocMemCache ... }` literal block.
        assert not re.search(
            r"CACHES\s*=\s*\{[^}]*LocMemCache", source, re.DOTALL
        ), 'CACHES must be resolved via build_caches(), not a hard-coded LocMemCache.'
        assert 'build_caches(' in source, \
            'settings.py must resolve CACHES through build_caches().'


class TestConceptGraphCacheRetired:
    """The concept-graph DB cache (#234/#240) was retired with the API+cache
    surface (#251, replaced by the local vocab mirror). build_caches must no
    longer emit the alias — a regression guard so the retired DB-cache /
    createcachetable path can't quietly return."""

    @pytest.mark.parametrize('kwargs', [
        dict(redis_url='', debug=False, environment='staging'),
        dict(redis_url='rediss://h:6379', debug=False, environment='production'),
        dict(redis_url='redis://localhost:6379', debug=True, environment='dev'),
    ])
    def test_no_concept_graph_alias(self, kwargs):
        assert 'concept_graph' not in build_caches(**kwargs)
