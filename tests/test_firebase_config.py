"""Firebase verification settings do not depend on DEBUG (#411).

`FIREBASE_SKIP_REVOCATION_CHECK` and `FIREBASE_PROJECT_ID` used to default off
`DEBUG`, so turning DEBUG on to read a traceback also turned revocation
checking off and pointed verification at a hard-coded `exact-test` project.
"""
import ast
import logging
import os
from pathlib import Path
from unittest import mock

import pytest

from accounts.providers.firebase import FirebaseTokenProvider
from exact.firebase_config import firebase_auth_settings

SETTINGS_PATH = Path(__file__).resolve().parents[1] / 'exact' / 'settings.py'


class TestFirebaseAuthSettings:
    @pytest.mark.parametrize('debug', [None, 'false', 'true'])
    def test_defaults_check_revocation_and_name_no_project_whatever_debug(self, debug):
        environ = {} if debug is None else {'DEBUG': debug}
        assert firebase_auth_settings(environ) == ('', False)

    @pytest.mark.parametrize('value', ['true', 'TRUE', '1', ' true '])
    @pytest.mark.parametrize('debug', ['false', 'true'])
    def test_explicit_opt_out_is_honoured_whatever_debug(self, value, debug):
        _, skip = firebase_auth_settings(
            {'DEBUG': debug, 'FIREBASE_SKIP_REVOCATION_CHECK': value}
        )
        assert skip is True

    @pytest.mark.parametrize('value', ['false', '0', '', 'no'])
    def test_anything_else_keeps_the_check_on_even_under_debug(self, value):
        _, skip = firebase_auth_settings(
            {'DEBUG': 'true', 'FIREBASE_SKIP_REVOCATION_CHECK': value}
        )
        assert skip is False

    def test_explicit_project_is_honoured(self):
        project, _ = firebase_auth_settings(
            {'DEBUG': 'true', 'FIREBASE_PROJECT_ID': 'demo-healthkey'}
        )
        assert project == 'demo-healthkey'


class TestSettingsWiring:
    def test_settings_resolve_through_the_helper(self):
        # Parsed, not pattern-matched: the old SKIP assignment spanned lines,
        # which a line regex misses.
        tree = ast.parse(SETTINGS_PATH.read_text())
        firebase_values = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = {
                    n.id for t in node.targets
                    for n in ast.walk(t) if isinstance(n, ast.Name)
                }
                if names & {'FIREBASE_PROJECT_ID', 'FIREBASE_SKIP_REVOCATION_CHECK'}:
                    firebase_values.append(node.value)
        assert firebase_values, 'settings.py no longer assigns the Firebase settings'
        for value in firebase_values:
            assert isinstance(value, ast.Call) and getattr(value.func, 'id', None) == (
                'firebase_auth_settings'
            ), 'FIREBASE_* settings must come from firebase_auth_settings() (#411)'
            assert not any(
                isinstance(n, ast.Name) and n.id == 'DEBUG' for n in ast.walk(value)
            ), 'FIREBASE_* settings must not be derived from DEBUG (#411)'

    def test_loaded_settings_match_the_helper(self, settings):
        assert (settings.FIREBASE_PROJECT_ID, settings.FIREBASE_SKIP_REVOCATION_CHECK) == (
            firebase_auth_settings(os.environ)
        )


class TestProviderUsesTheSetting:
    TOKEN = 'header.payload.signature'
    DECODED = {'iss': 'https://securetoken.google.com/p', 'uid': 'u1'}

    @pytest.mark.parametrize('skip, check_revoked', [(False, True), (True, False)])
    def test_verify_id_token_gets_check_revoked(self, settings, skip, check_revoked):
        settings.FIREBASE_SKIP_REVOCATION_CHECK = skip
        with mock.patch(
            'firebase_admin.auth.verify_id_token', return_value=self.DECODED
        ) as verify:
            claims = FirebaseTokenProvider().verify(self.TOKEN)
        verify.assert_called_once_with(self.TOKEN, check_revoked=check_revoked)
        assert claims.sub == 'u1'

    def test_a_disabled_user_is_refused_outright(self, settings):
        from firebase_admin import auth as firebase_auth
        from rest_framework.exceptions import AuthenticationFailed

        settings.FIREBASE_SKIP_REVOCATION_CHECK = False
        disabled = firebase_auth.UserDisabledError('The user record is disabled.')
        with mock.patch('firebase_admin.auth.verify_id_token', side_effect=disabled):
            with pytest.raises(AuthenticationFailed, match='disabled'):
                FirebaseTokenProvider().verify(self.TOKEN)

    def test_a_check_that_cannot_run_is_logged_as_a_warning(self, settings, caplog):
        settings.FIREBASE_SKIP_REVOCATION_CHECK = False
        with mock.patch(
            'firebase_admin.auth.verify_id_token', side_effect=PermissionError('no lookup')
        ), caplog.at_level(logging.WARNING, logger='accounts.providers.firebase'):
            assert FirebaseTokenProvider().verify(self.TOKEN) is None
        assert any(
            r.levelno == logging.WARNING and 'PermissionError' in r.getMessage()
            for r in caplog.records
        )


class TestStartupWarning:
    @pytest.mark.parametrize('skip, warned', [(True, True), (False, False)])
    def test_warns_once_firebase_is_initialised_with_the_check_off(
        self, monkeypatch, caplog, skip, warned
    ):
        import firebase_admin
        from exact import settings as exact_settings

        monkeypatch.setattr(firebase_admin, '_apps', {})
        monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', 'localhost:9099')
        monkeypatch.setattr(exact_settings, 'FIREBASE_SKIP_REVOCATION_CHECK', skip)
        with mock.patch('firebase_admin.initialize_app') as init, \
                caplog.at_level(logging.WARNING, logger='exact.settings'):
            exact_settings._init_firebase_admin()
        init.assert_called_once()
        messages = [r.getMessage() for r in caplog.records]
        assert any('FIREBASE_SKIP_REVOCATION_CHECK is on' in m for m in messages) is warned

    def test_no_warning_where_firebase_is_not_initialised(self, monkeypatch, caplog):
        import firebase_admin
        from exact import settings as exact_settings

        monkeypatch.setattr(firebase_admin, '_apps', {})
        monkeypatch.setattr(exact_settings, 'FIREBASE_SKIP_REVOCATION_CHECK', True)
        with mock.patch('firebase_admin.initialize_app', side_effect=RuntimeError('no')), \
                caplog.at_level(logging.WARNING, logger='exact.settings'):
            monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', 'localhost:9099')
            exact_settings._init_firebase_admin()
        assert not any(
            'FIREBASE_SKIP_REVOCATION_CHECK is on' in r.getMessage() for r in caplog.records
        )
