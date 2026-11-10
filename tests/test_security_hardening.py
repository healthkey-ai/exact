"""
Source-level guards for the #127 security-hardening settings.

Like tests/test_settings_security.py (#24), these read the source rather than
reloading settings — Django settings are frozen at import time, and the most
load-bearing behaviour (SECRET_KEY refusing a default in prod) only manifests
when DEBUG is off, which can't be toggled post-import. Asserting at the source
catches a regression before it ships rather than after a permissive deploy.
"""
import re
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = _ROOT / 'exact' / 'settings.py'
URLS_PATH = _ROOT / 'trials' / 'urls.py'
EXACT_URLS_PATH = _ROOT / 'exact' / 'urls.py'


def _settings_source():
    return SETTINGS_PATH.read_text()


def _urls_source():
    return URLS_PATH.read_text()


def _exact_urls_source():
    return EXACT_URLS_PATH.read_text()


class TestSecretKey:
    """Pre-#127 SECRET_KEY fell back to a publicly-known
    'django-insecure-...' literal unconditionally — a deploy missing the env
    var would silently run with a forgeable key. The fix keeps that fallback
    only for DEBUG/local and raises otherwise."""

    def test_secret_key_resolves_from_env_with_empty_default(self):
        source = _settings_source()
        assert re.search(
            r"SECRET_KEY\s*=\s*os\.environ\.get\(\s*['\"]SECRET_KEY['\"]\s*,\s*['\"]['\"]\s*\)",
            source,
        ), "SECRET_KEY must resolve from env with an empty (not insecure) default (#127)."

    def test_insecure_default_is_not_unconditional(self):
        source = _settings_source()
        # The insecure literal may still appear as the DEBUG/local fallback,
        # but never as the direct default of the env lookup.
        assert not re.search(
            r"SECRET_KEY\s*=\s*os\.environ\.get\(\s*['\"]SECRET_KEY['\"]\s*,\s*"
            r"['\"]django-insecure",
            source,
        ), "SECRET_KEY must not default directly to the insecure literal (#127)."

    def test_missing_key_raises_when_not_debug(self):
        source = _settings_source()
        assert 'ImproperlyConfigured' in source, \
            "A missing SECRET_KEY in a deployed env must raise ImproperlyConfigured (#127)."


class TestSecurityHeaders:
    """EXACT had no equivalent of SoC's _hardening.py. These settings must be
    present and applied for deployed (non-DEBUG) environments."""

    def test_hardening_block_is_debug_gated(self):
        source = _settings_source()
        assert re.search(r"if\s+not\s+DEBUG\s*:", source), \
            "Security headers must be applied under an `if not DEBUG:` guard (#127)."

    def test_required_security_settings_present(self):
        source = _settings_source()
        for name in (
            'SECURE_PROXY_SSL_HEADER',
            'SESSION_COOKIE_SECURE',
            'CSRF_COOKIE_SECURE',
            'SECURE_HSTS_SECONDS',
            'SECURE_HSTS_INCLUDE_SUBDOMAINS',
            'SECURE_HSTS_PRELOAD',
            'SECURE_REFERRER_POLICY',
            'SECURE_CONTENT_TYPE_NOSNIFF',
            'X_FRAME_OPTIONS',
        ):
            assert name in source, f"{name} must be set in settings.py (#127)."

    def test_x_frame_options_deny(self):
        source = _settings_source()
        assert re.search(r"X_FRAME_OPTIONS\s*=\s*['\"]DENY['\"]", source), \
            "X_FRAME_OPTIONS must be 'DENY' (#127)."

    def test_hsts_preload_is_production_only(self):
        source = _settings_source()
        # Canonical deploy value is 'prod' (docs/setup.md); accept 'production' too.
        assert re.search(
            r"SECURE_HSTS_PRELOAD\s*=\s*ENVIRONMENT\s+in\s*\(\s*['\"]prod['\"]",
            source,
        ), "HSTS preload must be enabled for the 'prod' environment (#127)."


class TestSwaggerGated:
    """Pre-#127 the Swagger/ReDoc schema view was public=True + AllowAny,
    serving the full API contract to anonymous callers."""

    def test_schema_view_not_allow_any(self):
        source = _urls_source()
        assert 'permissions.AllowAny' not in source, \
            "schema_view must not use AllowAny (#127)."

    def test_schema_view_requires_authentication(self):
        source = _urls_source()
        assert 'permissions.IsAuthenticated' in source, \
            "schema_view must require authentication (#127)."

    def test_schema_view_not_public(self):
        source = _urls_source()
        assert re.search(r"public\s*=\s*False", source), \
            "schema_view should be public=False so the schema is access-scoped (#127)."


class TestDrfTokenAuthGated:
    """Persistent DRF tokens (never-expiring, unscoped bearers minted from
    username/password) must not be enabled in production for a PHI service.
    They're gated behind ENABLE_DRF_TOKEN_AUTH, defaulting off outside
    local/DEBUG, and the `/api-token-auth/` endpoint is only registered when
    the flag is on (#153)."""

    def test_token_auth_flag_defaults_off_outside_local(self):
        source = _settings_source()
        # The default must fail closed: gated on DEBUG or an EXPLICIT
        # os.environ ENVIRONMENT=='local' (NOT the module-level ENVIRONMENT,
        # which defaults to 'local' when unset and would fail open in prod).
        assert re.search(
            r"_token_auth_default\s*=\s*\(?\s*\n?\s*'true'\s+if\s+\(?\s*DEBUG\s+or\s+"
            r"os\.environ\.get\(\s*['\"]ENVIRONMENT['\"]\s*\)\s*==\s*['\"]local['\"]",
            source,
        ), "ENABLE_DRF_TOKEN_AUTH default must fail closed on an unset ENVIRONMENT (#153)."

    def test_token_auth_default_rule_fails_closed(self):
        # Lock the truth table of the default-enable rule, including the
        # security-critical fail-closed case: ENVIRONMENT unset -> tokens OFF.
        def enabled(debug, environ):
            return debug or environ.get('ENVIRONMENT') == 'local'

        assert enabled(True, {}) is True                      # DEBUG
        assert enabled(False, {'ENVIRONMENT': 'local'}) is True   # explicit local
        assert enabled(False, {'ENVIRONMENT': 'prod'}) is False   # prod
        assert enabled(False, {'ENVIRONMENT': 'staging'}) is False
        assert enabled(False, {}) is False                    # unset -> fail closed

    def test_token_authentication_not_unconditional(self):
        source = _settings_source()
        # TokenAuthentication must be appended conditionally, never hard-listed
        # in DEFAULT_AUTHENTICATION_CLASSES.
        assert re.search(
            r"if\s+ENABLE_DRF_TOKEN_AUTH\s*:\s*\n\s*_AUTH_CLASSES\.append\(\s*"
            r"['\"]rest_framework\.authentication\.TokenAuthentication['\"]",
            source,
        ), "TokenAuthentication must be appended only when ENABLE_DRF_TOKEN_AUTH (#153)."

    def test_token_endpoint_is_flag_guarded(self):
        source = _exact_urls_source()
        assert re.search(
            r"if\s+getattr\(\s*settings,\s*['\"]ENABLE_DRF_TOKEN_AUTH['\"]",
            source,
        ), "/api-token-auth/ must be registered only when ENABLE_DRF_TOKEN_AUTH (#153)."

    def test_token_auth_enabled_in_test_env(self):
        # The test env sets ENVIRONMENT=local (exact/test_settings.py shim), so
        # the dev harness keeps working: flag on, TokenAuthentication active.
        from django.conf import settings
        from django.urls import reverse

        assert settings.ENABLE_DRF_TOKEN_AUTH is True
        assert 'rest_framework.authentication.TokenAuthentication' in \
            settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        assert reverse('api-token-auth')  # resolves without raising


class TestPersonIdLookupGated:
    """The server-side `?person_id=` resolver is a PHI IDOR (PROMOP service
    token unbound to the caller, no row-level authz). It's gated behind
    EXACT_ALLOW_PERSON_ID_LOOKUP, defaulting OFF outside local/DEBUG and
    failing closed on an unset ENVIRONMENT (#150/#108)."""

    def test_person_id_flag_defaults_off_outside_local(self):
        source = _settings_source()
        assert re.search(
            r"_person_id_lookup_default\s*=\s*\(?\s*\n?\s*'true'\s+if\s+\(?\s*DEBUG\s+or\s+"
            r"os\.environ\.get\(\s*['\"]ENVIRONMENT['\"]\s*\)\s*==\s*['\"]local['\"]",
            source,
        ), "EXACT_ALLOW_PERSON_ID_LOOKUP default must fail closed on an unset ENVIRONMENT (#150)."

    def test_person_id_default_rule_fails_closed(self):
        def enabled(debug, environ):
            return debug or environ.get('ENVIRONMENT') == 'local'

        assert enabled(True, {}) is True                          # DEBUG
        assert enabled(False, {'ENVIRONMENT': 'local'}) is True   # explicit local
        assert enabled(False, {'ENVIRONMENT': 'prod'}) is False   # prod
        assert enabled(False, {'ENVIRONMENT': 'staging'}) is False
        assert enabled(False, {}) is False                        # unset -> fail closed
