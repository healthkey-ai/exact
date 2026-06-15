"""
Smoke tests for production-safe defaults in exact/settings.py (#24).

These don't reload settings (Django settings are frozen by import time)
— they just guard against regressing the default values in the source.
Reading the file directly avoids the need for a subprocess to test
"what happens if DEBUG env is unset".
"""
import re
from pathlib import Path


SETTINGS_PATH = Path(__file__).resolve().parents[1] / 'exact' / 'settings.py'


def _settings_source():
    return SETTINGS_PATH.read_text()


class TestDebugDefault:
    """DEBUG must default to 'false' — pre-#24 it defaulted to 'true', which
    exposed stack traces / SQL / settings if the env var was missing or
    misnamed in a deploy. Catching a regression at the source-level beats
    catching it after a permissive deploy."""

    def test_debug_defaults_to_false(self):
        source = _settings_source()
        # Match: DEBUG = os.environ.get('DEBUG', '<default>')...
        match = re.search(r"DEBUG\s*=\s*os\.environ\.get\(\s*['\"]DEBUG['\"]\s*,\s*['\"]([^'\"]+)['\"]", source)
        assert match is not None, 'DEBUG env-resolution line not found in settings.py'
        default = match.group(1).lower()
        assert default == 'false', \
            f"DEBUG default must be 'false' for production safety (#24); got {default!r}"


class TestCorsAllowAllDefault:
    """CORS_ALLOW_ALL_ORIGINS = True (unconditional) was the pre-#24 setting,
    accepting browser requests from any origin. Replaced by an env-driven
    allowlist; the boolean kill-switch is opt-in only."""

    def test_cors_allow_all_origins_not_unconditional_true(self):
        source = _settings_source()
        # Reject the exact unconditional pattern. Allow env-resolved forms.
        assert not re.search(r"^CORS_ALLOW_ALL_ORIGINS\s*=\s*True\s*$", source, re.MULTILINE), \
            'CORS_ALLOW_ALL_ORIGINS must not be unconditionally True (#24); ' \
            'use an env-driven opt-in instead.'

    def test_cors_allow_all_origins_defaults_to_false(self):
        source = _settings_source()
        match = re.search(
            r"CORS_ALLOW_ALL_ORIGINS\s*=\s*\(?\s*"
            r"os\.environ\.get\(\s*['\"]CORS_ALLOW_ALL_ORIGINS['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            source,
        )
        assert match is not None, 'CORS_ALLOW_ALL_ORIGINS env-resolution line not found in settings.py'
        default = match.group(1).lower()
        assert default == 'false', \
            f"CORS_ALLOW_ALL_ORIGINS default must be 'false'; got {default!r}"

    def test_cors_allowed_origins_env_allowlist_is_declared(self):
        source = _settings_source()
        # Pre-#24 the codebase had no CORS_ALLOWED_ORIGINS at all — it relied
        # on the unconditional allow-all switch. The fix adds an env-driven
        # allowlist as the recommended path; assert it's present so a future
        # refactor can't silently remove it.
        assert 'CORS_ALLOWED_ORIGINS' in source, \
            'CORS_ALLOWED_ORIGINS must be present in settings.py (#24).'
        assert re.search(
            r"CORS_ALLOWED_ORIGINS\s*=\s*\[?\s*[\s\S]*?os\.environ\.get\(\s*['\"]CORS_ALLOWED_ORIGINS['\"]",
            source,
        ), 'CORS_ALLOWED_ORIGINS must resolve from the env var.'


class TestRedisTlsCertVerification:
    """rediss:// Celery SSL must not disable cert verification unconditionally.

    Pre-#157 both broker and backend pinned ssl.CERT_NONE, permitting MITM on
    Redis traffic that can carry patient-derived task results. CERT_NONE is now
    gated to local/DEBUG only; deployed envs use CERT_REQUIRED."""

    def test_cert_none_not_used_unconditionally(self):
        source = _settings_source()
        # The only CERT_NONE occurrence must be the local/DEBUG-gated ternary,
        # never a bare `ssl_cert_reqs': ssl.CERT_NONE` assignment as the value.
        assert not re.search(
            r"ssl_cert_reqs['\"]\s*:\s*ssl\.CERT_NONE\s*[,}]",
            source,
        ), 'rediss SSL must not hard-code CERT_NONE (#157).'

    def test_cert_required_is_present(self):
        source = _settings_source()
        assert 'ssl.CERT_REQUIRED' in source, \
            'Deployed rediss SSL must verify certs with CERT_REQUIRED (#157).'
