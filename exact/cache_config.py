"""Django cache backend resolution.

Factored out of settings.py so the choice between a shared Redis cache and a
per-process in-memory cache is unit-testable without reloading the frozen
settings module.

Why this matters (perf, #match-endpoint): `ValueOptions.all_options()` costs
~490 sequential DB queries to build and is cached for 1h. Under the deploy
config (`gunicorn --workers 4` on Cloud Run, multiple instances) a per-process
`LocMemCache` is never shared, so every cold worker re-pays the full build —
~6s per request over Cloud SQL network latency. Pointing the default cache at
the already-provisioned Redis (the same instance Celery uses) makes the blob
shared across all workers and instances: built once, then ~2ms per hit.
"""
import ssl
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

LOCMEM_BACKEND = "django.core.cache.backends.locmem.LocMemCache"
REDIS_BACKEND = "django.core.cache.backends.redis.RedisCache"

# redis-py's ConnectionPool.from_url() lets querystring args OVERRIDE kwargs
# passed via OPTIONS ("querystring arguments always win"). So a deployed
# REDIS_URL of `rediss://host?ssl_cert_reqs=none` would silently defeat the
# CERT_REQUIRED we set in OPTIONS, disabling TLS verification on a cache that
# also stores auth-token claims. Strip the TLS-policy params from the URL so
# our explicit OPTIONS value is authoritative.
_TLS_POLICY_QUERY_KEYS = {"ssl_cert_reqs", "ssl_check_hostname"}


def _strip_tls_policy_query(redis_url):
    parts = urlsplit(redis_url)
    if not parts.query:
        return redis_url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k not in _TLS_POLICY_QUERY_KEYS]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def build_caches(*, redis_url, debug, environment, redis_ca_certs=None):
    """Return the Django ``CACHES`` dict.

    ``redis_url`` must be the *raw* ``REDIS_URL`` env value (None/empty when
    unset) — not a defaulted connection string.

    We use a shared ``RedisCache`` only in deployed environments (not
    local/DEBUG) that have a ``REDIS_URL`` set. Local dev / CI / tests stay on a
    per-process ``LocMemCache`` even when ``REDIS_URL`` is present — the tracked
    `.env.example` ships ``REDIS_URL=redis://localhost:6379`` for Celery, and a
    developer without a running Redis must not have ordinary cache reads (auth
    token lookups, ``all_options()``) blow up with connection errors. A
    deployed env without ``REDIS_URL`` also falls back to LocMem: degraded
    (per-worker) but never crashing.

    For ``rediss://`` URLs the TLS cert policy mirrors the Celery config
    (#157): verification is required in deployed envs; ``redis_ca_certs``
    points at a private CA bundle when provided.
    """
    redis_url = (redis_url or "").strip()
    local = debug or environment == "local"
    if not redis_url or local:
        return {"default": {"BACKEND": LOCMEM_BACKEND}}

    options = {}
    if redis_url.startswith("rediss://"):
        # Deployed-only here (local returned above), so cert verification is
        # always required. Strip any TLS-policy querystring first so OPTIONS is
        # authoritative and the URL can't downgrade it (see above).
        redis_url = _strip_tls_policy_query(redis_url)
        options["ssl_cert_reqs"] = ssl.CERT_REQUIRED
        if redis_ca_certs:
            options["ssl_ca_certs"] = redis_ca_certs

    default = {
        "BACKEND": REDIS_BACKEND,
        "LOCATION": redis_url,
        # Namespace by environment so a staging and prod that happen to share a
        # Redis instance (and the Celery broker living in the same DB) can't
        # read each other's cached blobs. NOTE: KEY_PREFIX scopes reads/writes
        # but NOT cache.clear() — that issues FLUSHDB and would wipe the whole
        # logical DB (incl. the Celery broker). No code calls cache.clear()
        # today; if that changes, give the cache its own Redis DB index first.
        "KEY_PREFIX": environment or "exact",
    }
    if options:
        default["OPTIONS"] = options
    return {"default": default}
