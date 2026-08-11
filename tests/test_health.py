"""The /healthz contract the platform's load balancer depends on.

The point of the endpoint is that it fails when the service cannot serve real
traffic — a probe that only proves Django booted stays green through a deploy
whose migrations never ran. So the 503 path is the test that matters.
"""
import ast
import inspect
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from exact import health


class _BrokenConnection:
    """Stands in for the default connection; every cursor raises.

    Patched at `exact.health.connection` rather than on `django.db.connection`:
    that is a proxy whose attribute writes land on the live DatabaseWrapper,
    which then outlives the test.
    """

    def __init__(self, error):
        self._error = error

    def cursor(self):
        raise self._error


@pytest.mark.django_db
class TestHealthz:
    def test_reports_ok_when_the_database_answers(self, client):
        response = client.get('/healthz')
        assert response.status_code == 200
        assert response.json() == {'status': 'ok', 'database': 'ok'}

    def test_reports_503_when_the_database_is_unreachable(self, client, monkeypatch):
        monkeypatch.setattr(
            'exact.health.connection',
            _BrokenConnection(RuntimeError('could not connect to server')),
        )
        response = client.get('/healthz')
        assert response.status_code == 503
        assert response.json()['status'] == 'error'

    def test_does_not_leak_the_underlying_error(self, client, monkeypatch):
        """The body reaches whatever can hit the probe, so it must not carry a
        driver message — those name hosts, ports and database names."""
        secret = 'host=10.0.0.7 dbname=exact_prod password=hunter2'
        monkeypatch.setattr(
            'exact.health.connection', _BrokenConnection(RuntimeError(secret))
        )
        assert secret not in client.get('/healthz').content.decode()

    def test_is_reachable_where_the_rest_of_the_api_is_default_deny(self, client):
        """A load-balancer probe cannot carry credentials, so this endpoint has
        to be the explicit exception to IsAuthenticated — asserted against a
        protected endpoint in the same breath, so the two cannot drift."""
        assert client.get('/trials/').status_code == 401
        assert client.get('/healthz').status_code == 200

    def test_reaches_no_database_but_the_default(self):
        """An outage on the externally-owned `trials` host must not pull this
        service out of rotation; the trial endpoints degrade on their own.

        Two ways to break that, so both are checked: naming the alias directly
        via `connections['trials']`, and — the easier one to reach by accident —
        an ORM read, since DATABASE_ROUTERS sends every trials-app model to that
        alias with no `connections` literal anywhere.

        Checked over the parsed module rather than its text: a substring search
        matches the word in a comment, misses `from django.db import connection,
        connections`, and cannot see an ORM query at all.
        """
        tree = ast.parse(Path(inspect.getsourcefile(health)).read_text())

        imported = {
            (node.module or '', alias.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        } | {
            ('', alias.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        assert ('django.db', 'connections') not in imported, (
            "/healthz must not import `connections` — indexing it is how a "
            'non-default alias gets reached.'
        )
        assert ('django.db', 'connection') in imported, \
            '/healthz must probe the default connection'

        model_imports = sorted(
            f'{module}.{name}' for module, name in imported
            if module.startswith('trials') or module == 'django.apps'
        )
        assert not model_imports, (
            f'/healthz imports {model_imports} — an ORM read is routed to the '
            '`trials` alias by DATABASE_ROUTERS, so it would probe the external '
            'host. Use a cursor on the default connection.'
        )

    def test_does_not_probe_the_external_trials_database(self, client, monkeypatch):
        """The behavioural counterpart, where an alias exists to be broken."""
        if 'trials' not in django_settings.DATABASES:
            pytest.skip('no separate trials database configured in this environment')

        from django.db import connections

        def forbidden(*args, **kwargs):
            raise AssertionError('/healthz must not touch the trials connection')

        monkeypatch.setattr(connections['trials'], 'cursor', forbidden)
        assert client.get('/healthz').status_code == 200
