"""Strict, metadata-only schema-subset preflight for the routed trial catalog."""
import hashlib
import json
import re
from datetime import datetime, timezone

from django.apps import apps
from django.conf import settings
from django.db import connections, router
from django.db.models import NOT_PROVIDED


class CatalogSchemaError(RuntimeError):
    pass


def check_catalog_configuration():
    """Matching startup is read-only; administrative copy initialization is separate."""
    if 'trials' not in settings.DATABASES:
        return
    unsafe = []
    if getattr(settings, 'TRIALS_DB_MIGRATE', False):
        unsafe.append('TRIALS_DB_MIGRATE')
    if getattr(settings, 'TRIALS_DB_TOLERATE_MISSING_COLUMNS', False):
        unsafe.append('TRIALS_DB_TOLERATE_MISSING_COLUMNS')
    import os
    if os.environ.get('TRIALS_DATABASE_INIT_FROM_BACKUP', '').lower() in ('1', 'true', 'yes', 'on'):
        unsafe.append('TRIALS_DATABASE_INIT_FROM_BACKUP')
    if unsafe:
        raise CatalogSchemaError(
            'Read-only catalog startup refuses: ' + ', '.join(unsafe)
            + '. Initialize an explicitly owned copy separately, then disable write/tolerance flags.'
        )
    from trials.db_compat import _same_database
    if _same_database(settings.DATABASES.get('default', {}), settings.DATABASES['trials']):
        raise CatalogSchemaError(
            'The default auth database and external trials catalog resolve to the same '
            'configured database. Use a separate EXACT-owned auth database and a '
            'read-only catalog role; bootstrap migrations must not write to CB.'
        )


def _expected_typmod(db_type):
    if db_type.endswith('[]'):
        return _expected_typmod(db_type[:-2])
    numeric = re.fullmatch(r'numeric\((\d+),\s*(\d+)\)', db_type)
    if numeric:
        precision, scale = map(int, numeric.groups())
        return ((precision << 16) | scale) + 4
    varchar = re.fullmatch(r'varchar\((\d+)\)', db_type)
    if varchar:
        return int(varchar[1]) + 4
    return -1


def _empty_json_default(expression):
    # Never evaluate a default expression. Accept only the literal empty JSONB.
    text = (expression or '').strip()
    while text.startswith('(') and text.endswith(')'):
        text = text[1:-1].strip()
    return bool(re.fullmatch(r"'\[\]'\s*::\s*jsonb", text, re.IGNORECASE))


def inspect_trials_schema():
    """Inspect every current concrete trials model, including automatic throughs.

    Use a dedicated connection: no caller transaction is changed and no socket
    survives into preloaded/forked workers. All SQL runs in one read-only snapshot.
    """
    check_catalog_configuration()
    models = sorted(apps.get_app_config('trials').get_models(include_auto_created=True),
                    key=lambda model: model._meta.label_lower)
    aliases = {router.db_for_read(model) or 'default' for model in models}
    if len(aliases) != 1:
        raise CatalogSchemaError('Trial model reads must resolve to one catalog database alias.')
    alias = aliases.pop()
    connection = connections[alias].copy(alias=alias)
    if connection.vendor != 'postgresql':
        raise CatalogSchemaError('The trial schema contract requires PostgreSQL/PostGIS.')
    result = {'alias': alias, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'tables': [], 'columns_compared': 0, 'errors': []}
    expected_contract = [
        (f'{model._meta.db_table}.{field.column}', field.db_type(connection), field.null,
         repr(field.db_default) if field.db_default is not NOT_PROVIDED else None)
        for model in models for field in model._meta.local_concrete_fields
    ]
    type_oids = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cursor.execute("SET LOCAL statement_timeout = '10s'")
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute('''
                SELECT current_database(), current_user, current_setting('search_path'),
                       inet_server_addr()::text, inet_server_port(),
                       current_setting('server_version_num'),
                       (SELECT oid FROM pg_database WHERE datname=current_database()),
                       current_setting('transaction_read_only')
            ''')
            result['database_identity'] = dict(zip(
                ('database', 'role', 'search_path', 'server_address', 'server_port',
                 'server_version', 'database_oid', 'transaction_read_only'), cursor.fetchone()))
            for model in models:
                table = model._meta.db_table
                cursor.execute('''
                    SELECT c.oid, n.nspname, c.relkind,
                           has_schema_privilege(c.relnamespace, 'USAGE')
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE c.oid=to_regclass(%s)
                ''', [connection.ops.quote_name(table)])
                relation = cursor.fetchone()
                if not relation:
                    result['errors'].append(f'{table}: missing relation')
                    continue
                oid, schema, kind, schema_usage = relation
                result['tables'].append({'table': table, 'schema': schema, 'oid': oid})
                if kind not in ('r', 'p'):
                    result['errors'].append(f'{table}: unsupported relation kind {kind}; require table metadata')
                if not schema_usage:
                    result['errors'].append(f'{table}: schema USAGE privilege missing')
                cursor.execute('''
                    SELECT a.attname, a.atttypid, a.atttypmod, a.attnotnull,
                           pg_get_expr(d.adbin,d.adrelid),
                           has_column_privilege(a.attrelid,a.attnum,'SELECT'),
                           format_type(a.atttypid,a.atttypmod),
                           CASE WHEN t.typname IN ('geometry','geography')
                                THEN postgis_typmod_type(a.atttypmod) END,
                           CASE WHEN t.typname IN ('geometry','geography')
                                THEN postgis_typmod_srid(a.atttypmod) END,
                           CASE WHEN t.typname IN ('geometry','geography')
                                THEN postgis_typmod_dims(a.atttypmod) END
                    FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid
                    LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                    WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped
                ''', [oid])
                columns = {row[0]: row[1:] for row in cursor.fetchall()}
                for field in model._meta.local_concrete_fields:
                    name = f'{table}.{field.column}'
                    db_type = field.db_type(connection)
                    declared_default = field.db_default is not NOT_PROVIDED
                    column = columns.get(field.column)
                    if column is None:
                        result['errors'].append(f'{name}: missing column')
                        continue
                    actual_oid, typmod, not_null, default, readable, actual_type, geom, srid, dims = column
                    result['columns_compared'] += 1
                    if db_type not in type_oids:
                        cursor.execute('SELECT to_regtype(%s)::oid', [db_type])
                        type_oids[db_type] = cursor.fetchone()[0]
                    spatial = hasattr(field, 'geom_type')
                    type_matches = actual_oid == type_oids[db_type]
                    if spatial:
                        type_matches &= (geom or '').upper() == field.geom_type.upper()
                        type_matches &= srid == field.srid and dims == field.dim
                    else:
                        type_matches &= typmod == _expected_typmod(db_type)
                    if not type_matches:
                        result['errors'].append(f'{name}: expected {db_type}, found {actual_type}')
                    if not_null == field.null:
                        result['errors'].append(f'{name}: nullability mismatch (expected null={field.null})')
                    if not readable:
                        result['errors'].append(f'{name}: SELECT privilege missing')
                    if declared_default and not (
                        db_type == 'jsonb' and field.db_default == [] and _empty_json_default(default)
                    ):
                        result['errors'].append(f'{name}: required database default is absent or incompatible')
            cursor.execute('COMMIT')
    finally:
        connection.close()
    result['contract_sha256'] = hashlib.sha256(
        json.dumps(expected_contract, sort_keys=True).encode()).hexdigest()
    result['compatible'] = not result['errors']
    return result


def require_trials_schema():
    result = inspect_trials_schema()
    if not result['compatible']:
        raise CatalogSchemaError('Trial catalog is incompatible:\n' + '\n'.join(result['errors']))
    return result
