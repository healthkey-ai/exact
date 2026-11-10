import json
import os
import runpy
import subprocess
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, models
from django.contrib.gis.db.models import PointField

from trials import schema_contract

pytestmark = pytest.mark.django_db


class SchemaFixture(models.Model):
    id = models.IntegerField(primary_key=True)
    criterion = models.JSONField(default=list, db_default=[])
    threshold = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    location = PointField(srid=4326, null=True)

    class Meta:
        app_label = 'schema_fixture_659'
        db_table = 'schema_fixture_659'


@pytest.fixture
def isolated_catalog(monkeypatch):
    """Only our own schema in the dedicated local pytest DB, never a CB catalog."""
    assert connection.settings_dict['NAME'].startswith('test_')
    assert connection.settings_dict['HOST'] in ('localhost', '127.0.0.1')
    schema = 'schema659_' + uuid4().hex
    owner = connection.copy()
    quoted = owner.ops.quote_name(schema)
    with owner.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA {quoted}')
    catalog = connection.copy()
    catalog.settings_dict.setdefault('OPTIONS', {})['options'] = f'-c search_path={schema},public'
    with catalog.schema_editor() as editor:
        editor.create_model(SchemaFixture)
    config = SimpleNamespace(get_models=lambda include_auto_created: [SchemaFixture])
    monkeypatch.setattr(schema_contract.apps, 'get_app_config', lambda _: config)
    monkeypatch.setattr(schema_contract.router, 'db_for_read', lambda _: 'default')
    monkeypatch.setattr(schema_contract, 'connections', {'default': catalog})
    try:
        yield catalog
    finally:
        catalog.close()
        with owner.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA {quoted} CASCADE')
        owner.close()


def test_extra_columns_allowed_and_search_path_identity_is_recorded(isolated_catalog):
    with isolated_catalog.cursor() as cursor:
        cursor.execute('ALTER TABLE schema_fixture_659 ADD COLUMN cb_extra text')
    result = schema_contract.require_trials_schema()
    assert result['compatible']
    assert result['columns_compared'] == 4
    assert result['tables'][0]['schema'].startswith('schema659_')
    assert len(result['contract_sha256']) == 64
    assert result['database_identity']['database'] == connection.settings_dict['NAME']
    assert result['database_identity']['transaction_read_only'] == 'on'


@pytest.mark.parametrize('ddl,expected', [
    ('ALTER TABLE schema_fixture_659 DROP COLUMN criterion', 'missing column'),
    ('ALTER TABLE schema_fixture_659 ALTER COLUMN criterion TYPE text', 'expected jsonb'),
    ('ALTER TABLE schema_fixture_659 ALTER COLUMN criterion DROP NOT NULL', 'nullability mismatch'),
    ('ALTER TABLE schema_fixture_659 ALTER COLUMN criterion DROP DEFAULT', 'required database default'),
    ('ALTER TABLE schema_fixture_659 ALTER COLUMN criterion SET DEFAULT \'{}\'::jsonb', 'required database default'),
    ('ALTER TABLE schema_fixture_659 ALTER COLUMN threshold TYPE numeric(10,2)', 'expected numeric(12, 2)'),
    ('ALTER TABLE schema_fixture_659 ALTER COLUMN location TYPE geometry(Point,3857)', 'expected geometry'),
    ('DROP TABLE schema_fixture_659', 'missing relation'),
])
def test_drift_fails_before_orm_reads(isolated_catalog, ddl, expected):
    before = schema_contract.require_trials_schema()
    with isolated_catalog.cursor() as cursor:
        cursor.execute(ddl)
    result = schema_contract.inspect_trials_schema()
    assert result['compatible'] is False
    assert result['contract_sha256'] == before['contract_sha256']
    assert any(expected in error for error in result['errors'])
    with pytest.raises(schema_contract.CatalogSchemaError, match='incompatible'):
        schema_contract.require_trials_schema()
    with pytest.raises(CommandError):
        call_command('check_trials_schema', stdout=StringIO())


def test_command_reports_evidence_and_does_not_run_ddl(isolated_catalog):
    output = StringIO()
    call_command('check_trials_schema', stdout=output)
    result = json.loads(output.getvalue())
    assert result['compatible'] is True
    assert result['errors'] == []


def test_required_default_is_not_evaluated(isolated_catalog):
    with isolated_catalog.cursor() as cursor:
        cursor.execute("ALTER TABLE schema_fixture_659 ALTER COLUMN criterion SET DEFAULT jsonb_build_array()")
    result = schema_contract.inspect_trials_schema()
    assert any('required database default' in error for error in result['errors'])


def test_domain_does_not_silently_pass_as_its_base_type(isolated_catalog):
    with isolated_catalog.cursor() as cursor:
        cursor.execute('CREATE DOMAIN domain659 AS jsonb')
        cursor.execute('ALTER TABLE schema_fixture_659 ALTER COLUMN criterion TYPE domain659')
    result = schema_contract.inspect_trials_schema()
    assert any('expected jsonb' in error for error in result['errors'])


@pytest.mark.parametrize('setting', ['TRIALS_DB_MIGRATE', 'TRIALS_DB_TOLERATE_MISSING_COLUMNS'])
def test_split_catalog_startup_refuses_write_or_deferral_flags_without_connecting(setting):
    with patch('django.conf.settings.DATABASES', {'default': {}, 'trials': {}}), \
         patch.object(schema_contract.settings, setting, True), \
         patch.object(schema_contract, 'connections') as connections:
        with pytest.raises(schema_contract.CatalogSchemaError, match=setting):
            schema_contract.inspect_trials_schema()
        connections.__getitem__.assert_not_called()


def test_split_catalog_startup_refuses_restore_before_connecting(monkeypatch):
    monkeypatch.setenv('TRIALS_DATABASE_INIT_FROM_BACKUP', 'true')
    with patch('django.conf.settings.DATABASES', {'default': {}, 'trials': {}}), \
         patch.object(schema_contract, 'connections') as connections:
        with pytest.raises(CommandError, match='TRIALS_DATABASE_INIT_FROM_BACKUP'):
            call_command('check_trials_schema', configuration_only=True)
        connections.__getitem__.assert_not_called()


def test_real_standalone_schema_includes_every_model_and_all_new_columns():
    result = schema_contract.require_trials_schema()
    assert result['compatible'] is True
    assert result['columns_compared'] > 500
    assert 'trials_marker' not in {item['table'] for item in result['tables']}
    assert 'trials_rawdataitem' not in {item['table'] for item in result['tables']}


@pytest.mark.parametrize('expression,expected', [
    ("has_schema_privilege(c.relnamespace, 'USAGE')", 'schema USAGE'),
    ("has_column_privilege(a.attrelid,a.attnum,'SELECT')", 'SELECT privilege'),
])
def test_privilege_failures_are_not_misread_as_compatible(isolated_catalog, monkeypatch, expression, expected):
    original_copy = isolated_catalog.copy

    def copy_with_denied_privilege(*args, **kwargs):
        copied = original_copy(*args, **kwargs)

        def deny(execute, sql, params, many, context):
            # Inject a denied metadata result, without creating cluster roles.
            return execute(sql.replace(expression, 'false'), params, many, context)

        copied.execute_wrappers.append(deny)
        return copied

    monkeypatch.setattr(isolated_catalog, 'copy', copy_with_denied_privilege)
    result = schema_contract.inspect_trials_schema()
    assert result['compatible'] is False
    assert any(expected in error for error in result['errors'])


def test_direct_wsgi_startup_refuses_drift_before_application_is_returned(isolated_catalog):
    with isolated_catalog.cursor() as cursor:
        cursor.execute('ALTER TABLE schema_fixture_659 DROP COLUMN criterion')
    with patch('django.core.wsgi.get_wsgi_application', return_value=object()):
        with pytest.raises(schema_contract.CatalogSchemaError, match='missing column'):
            runpy.run_module('exact.wsgi')


def test_entrypoint_configuration_check_precedes_bootstrap_and_gunicorn(tmp_path):
    fake_python = tmp_path / 'python'
    log = tmp_path / 'commands.log'
    fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SCHEMA_TEST_LOG"\nexit 19\n')
    fake_python.chmod(0o700)
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'SCHEMA_TEST_LOG': str(log)}
    result = subprocess.run(['bash', str(root / 'docker/entrypoint.sh')], cwd=root,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 19
    assert log.read_text().splitlines() == ['manage.py check_trials_schema --configuration-only']
    assert 'Running migrations' not in result.stdout
    assert 'Starting gunicorn' not in result.stdout


def test_split_router_checks_only_catalog_and_forbids_external_migrations(isolated_catalog, monkeypatch):
    from exact.db_router import TrialsDatabaseRouter

    monkeypatch.setattr(schema_contract.router, 'db_for_read', lambda _: 'trials')
    monkeypatch.setattr(schema_contract, 'connections', {'trials': isolated_catalog})
    with patch('django.conf.settings.DATABASES', {'default': {'NAME': 'auth'}, 'trials': {'NAME': 'catalog'}}):
        result = schema_contract.require_trials_schema()
        assert result['alias'] == 'trials'
        assert result['database_identity']['transaction_read_only'] == 'on'
        db_router = TrialsDatabaseRouter()
        assert db_router.db_for_read(SchemaFixture) is None  # fixture is not a trials model
        assert db_router.allow_migrate('trials', 'trials') is False
        assert db_router.allow_migrate('default', 'trials') is False


@pytest.mark.parametrize('default,catalog', [
    ({'NAME': 'catalog', 'HOST': 'localhost'}, {'NAME': 'catalog', 'HOST': '127.0.0.1', 'PORT': '5432'}),
    ({'NAME': 'catalog', 'HOST': 'LOCALHOST.'}, {'NAME': 'catalog', 'HOST': '::1'}),
    ({'NAME': 'catalog', 'USER': 'auth_owner', 'OPTIONS': {'options': '-c search_path=auth'}},
     {'NAME': 'catalog', 'USER': 'catalog_reader', 'OPTIONS': {'options': '-c search_path=public'}}),
    ({'NAME': 'catalog', 'OPTIONS': {'host': '/cloudsql/instance'}},
     {'NAME': 'catalog', 'HOST': '/cloudsql/instance', 'PORT': 5432}),
])
def test_shared_auth_catalog_database_is_refused_without_connecting(default, catalog):
    with patch('django.conf.settings.DATABASES', {'default': default, 'trials': catalog}), \
         patch.object(schema_contract, 'connections') as connections:
        with pytest.raises(CommandError, match='same configured database'):
            call_command('check_trials_schema', configuration_only=True)
        connections.__getitem__.assert_not_called()


def test_distinct_auth_catalog_configuration_can_be_checked_without_connecting():
    with patch('django.conf.settings.DATABASES', {'default': {'NAME': 'auth'}, 'trials': {'NAME': 'catalog'}}), \
         patch.object(schema_contract, 'connections') as connections:
        output = StringIO()
        call_command('check_trials_schema', configuration_only=True, stdout=output)
        assert 'OK' in output.getvalue()
        connections.__getitem__.assert_not_called()
