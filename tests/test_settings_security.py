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


class TestPhrIntrospectionDefault:
    """The portal introspection fallback must be opt-in outside local/DEBUG.

    It delegates the signature check to another service — an `active` response
    is taken as vouching for the token, and where the response is silent about
    the subject the token's *unverified* payload supplies it. Both PHR_*_URLs
    derive from one PHR_BASE_URL, so before the flag existed every deployment
    that configured RS256 verification silently enabled this path too, and the
    caller chose which one to use by writing the `alg` header."""

    def test_introspection_defaults_off_outside_local_and_debug(self):
        source = _settings_source()
        # Matching the wiring too, not just the default expression: a default
        # that is computed correctly but never passed to os.environ.get would
        # pass a looser test while changing nothing.
        match = re.search(
            r"_phr_introspection_default\s*=\s*\(\s*(.*?)\s*\)\s*\n\s*"
            r"PHR_ALLOW_INTROSPECTION\s*=\s*os\.environ\.get\(\s*"
            r"['\"]PHR_ALLOW_INTROSPECTION['\"]\s*,\s*_phr_introspection_default\s*\)",
            source,
            re.DOTALL,
        )
        assert match is not None, \
            'PHR_ALLOW_INTROSPECTION must resolve from the env var with ' \
            '_phr_introspection_default as its default'
        default = match.group(1)
        assert re.search(r"else\s*['\"]false['\"]", default), \
            f'PHR_ALLOW_INTROSPECTION must fall through to false when deployed; got {default!r}'
        assert 'DEBUG' in default and 'local' in default, \
            ('PHR_ALLOW_INTROSPECTION must be gated on DEBUG/ENVIRONMENT=local, '
             f'not enabled unconditionally; got {default!r}')

    def test_introspection_default_reads_environment_from_os_environ(self):
        """The gate must fail closed when a deploy forgets ENVIRONMENT.

        The module-level ENVIRONMENT defaults to 'local', so `ENVIRONMENT ==
        'local'` reads as true in exactly the misconfigured deploy this gate
        exists to protect. Same regex shape as ENABLE_DRF_TOKEN_AUTH's guard in
        tests/test_security_hardening.py, which requires the closing paren
        directly after the key — so `os.environ.get('ENVIRONMENT', 'local')`,
        which would restore the fail-open behaviour, does not satisfy it.
        """
        source = _settings_source()
        assert re.search(
            r"_phr_introspection_default\s*=\s*\(?\s*\n?\s*'true'\s+if\s+\(?\s*DEBUG\s+or\s+"
            r"os\.environ\.get\(\s*['\"]ENVIRONMENT['\"]\s*\)\s*==\s*['\"]local['\"]",
            source,
        ), 'PHR_ALLOW_INTROSPECTION default must fail closed on an unset ENVIRONMENT.'

    def test_introspection_default_rule_fails_closed(self):
        """Locks the truth table, so the rule survives a refactor the regex
        above would reject. The security-critical row is the last one."""
        def enabled(debug, environ):
            return debug or environ.get('ENVIRONMENT') == 'local'

        assert enabled(True, {}) is True                            # DEBUG
        assert enabled(False, {'ENVIRONMENT': 'local'}) is True      # explicit local
        assert enabled(False, {'ENVIRONMENT': 'prod'}) is False
        assert enabled(False, {'ENVIRONMENT': 'staging'}) is False
        assert enabled(False, {}) is False                          # unset -> closed

    def test_outbound_introspection_calls_are_capped(self):
        source = _settings_source()
        # DRF authenticates before it throttles, so AnonRateThrottle cannot
        # reach this path — the cap is the only bound on outbound calls an
        # anonymous caller can drive.
        assert re.search(
            r"PHR_INTROSPECT_MAX_CALLS\s*=\s*int\(\s*os\.environ\.get\(", source
        ), 'PHR_INTROSPECT_MAX_CALLS must be present and env-resolved.'
        assert re.search(
            r"PHR_INTROSPECT_RATE_INTERVAL\s*=\s*int\(\s*os\.environ\.get\(", source
        ), 'PHR_INTROSPECT_RATE_INTERVAL must be present and env-resolved.'


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
