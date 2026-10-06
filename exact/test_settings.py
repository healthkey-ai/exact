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

# Same reasoning for the PROMOP credentials: `exact.settings` reads them from the
# environment and from BASE_DIR/.env, so a developer who has configured a real
# local promop (as .env.example instructs) would otherwise have those values
# reach any test that builds a client without passing every field explicitly —
# turning assertions about what EXACT sends into assertions about their shell.
# A test that wants a credential passes one; tests of the unconfigured fallbacks
# delete the setting.
PROMOP_BASE = ""
PROMOP_SERVICE_TOKEN = ""
PROMOP_OAUTH_CLIENT_ID = ""
PROMOP_OAUTH_CLIENT_SECRET = ""
PROMOP_OAUTH_SCOPE = ""
PROMOP_OAUTH_TOKEN_URL = ""
PROMOP_API_BASE = ""
PROMOP_VOCAB_BASE = ""
PROMOP_VOCAB_SERVICE_TOKEN = ""
PROMOP_VOCAB_OAUTH_CLIENT_ID = ""
PROMOP_VOCAB_OAUTH_CLIENT_SECRET = ""
PROMOP_VOCAB_OAUTH_SCOPE = ""
PROMOP_VOCAB_OAUTH_TOKEN_URL = ""
