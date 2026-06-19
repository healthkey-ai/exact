"""Test settings shim.

Sets ENVIRONMENT=local BEFORE importing the real settings, so security controls
that fail closed on an *unset* ENVIRONMENT — e.g. ENABLE_DRF_TOKEN_AUTH (#153) —
enable their dev/test behavior. This must run before exact.settings executes,
which a conftest can't guarantee (pytest-django sets up Django during
pytest_load_initial_conftests, before conftest import). pytest.ini points
DJANGO_SETTINGS_MODULE here.
"""
import os

os.environ.setdefault("ENVIRONMENT", "local")

from exact.settings import *  # noqa: E402,F401,F403

# Force a per-process in-memory cache for the test run regardless of any
# ambient REDIS_URL in the developer's shell — tests must not read/write a
# real (possibly shared) Redis. The all_options() caching behavior is
# backend-agnostic, so LocMemCache exercises the same code path.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}
