"""Exercise real standalone transitions without touching the shared pytest schema."""
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection


@pytest.mark.django_db
def test_real_0026_to_0028_preserves_retired_tables_rows_and_wide_rbc_values():
    database = connection.settings_dict
    assert database['NAME'].startswith('test_')
    schema = 'migration659_' + uuid4().hex
    owner = connection.copy()
    quoted = owner.ops.quote_name(schema)
    with owner.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA {quoted}')
        # Shadow the public migration recorder before constructing an executor.
        cursor.execute(f'''CREATE TABLE {quoted}.django_migrations (
            id bigserial PRIMARY KEY, app varchar(255) NOT NULL,
            name varchar(255) NOT NULL, applied timestamptz NOT NULL)''')
    root = Path(__file__).resolve().parents[2]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('DATABASE_', 'TRIALS_', 'PATIENT_', 'PROMOP_', 'EXACT_', 'DJANGO_'))}
    env.update({
        'DOTENV_PATH': '/dev/null', 'ENVIRONMENT': 'local',
        'DJANGO_SETTINGS_MODULE': 'exact.test_settings',
        'DATABASE_NAME': database['NAME'], 'DATABASE_USER': database['USER'],
        'DATABASE_PASSWORD': database['PASSWORD'], 'DATABASE_HOST': database['HOST'],
        'DATABASE_PORT': str(database['PORT'] or '5432'),
        'PGOPTIONS': f'-c search_path={schema},public',
        'PYTHONPATH': f'{root}:{root / "matcher_app"}',
    })
    script = '''
import json, django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from tests.management.test_trials_schema_contract import CB_0449_SOURCES
executor = MigrationExecutor(connection)
old = ('trials', '0026_myeloma_related_disorders_seed')
executor.migrate([old])
old_apps = executor.loader.project_state([old]).apps
marker = old_apps.get_model('trials', 'Marker').objects.create(code='migration659', title='Migration 659')
category = old_apps.get_model('trials', 'MarkerCategory').objects.create(code='cytogenic', title='Cytogenic Markers')
marker.categories.add(category)
raw = old_apps.get_model('trials', 'RawDataItem').objects.create(record_id='migration659', source_name='fixture', extracted_data={'keep': True})
trial = old_apps.get_model('trials', 'Trial').objects.create(disease='multiple myeloma', red_blood_cell_count_min=1000000000)
executor = MigrationExecutor(connection)
middle = ('trials', '0027_cb_marker_catalogs')
executor.migrate([middle])
mid_apps = executor.loader.project_state([middle]).apps
assert mid_apps.get_model('trials', 'CytogenicMarker').objects.get(code='migration659').title == marker.title
executor = MigrationExecutor(connection)
latest = ('trials', '0028_cb0449_catalog_contract')
executor.migrate([latest])
new_apps = executor.loader.project_state([latest]).apps
assert old_apps.get_model('trials', 'Marker').objects.get(pk=marker.pk).title == marker.title
assert marker.categories.count() == 1
assert old_apps.get_model('trials', 'RawDataItem').objects.get(pk=raw.pk).extracted_data == {'keep': True}
for name in ('Marker', 'MarkerCategory', 'MarkerCategoryConnection', 'RawDataItem'):
    try:
        new_apps.get_model('trials', name)
    except LookupError:
        pass
    else:
        raise AssertionError(name + ' still in current state')
loaded = new_apps.get_model('trials', 'Trial').objects.get(pk=trial.pk)
assert loaded.red_blood_cell_count_min == 1000000000
for source in CB_0449_SOURCES:
    assert getattr(loaded, 'omop_' + source) == []
print(json.dumps({'transition': '0026->0027->0028', 'legacy_preserved': True, 'new_fields': len(CB_0449_SOURCES)}))
'''
    try:
        result = subprocess.run([sys.executable, '-c', script], env=env, cwd=root,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout.splitlines()[-1]) == {
            'transition': '0026->0027->0028', 'legacy_preserved': True, 'new_fields': 43,
        }
    finally:
        with owner.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA {quoted} CASCADE')
        owner.close()
