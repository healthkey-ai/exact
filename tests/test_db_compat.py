"""Tests for the stale-corpus tolerance in trials.db_compat.

The situation being modelled is a trials database whose schema predates the
models — see exact#360. It cannot be reproduced by dropping a column from the
test database, because the test database is built from the migrations that add
those columns. So introspection is stubbed instead, and the assertions are
about the decision the module makes given what it finds.
"""
import pytest

from trials import db_compat
from trials.models import Trial


@pytest.fixture(autouse=True)
def clear_cache():
    db_compat._cache.clear()
    yield
    db_compat._cache.clear()


def _stub_columns(monkeypatch, columns):
    """Make introspection report exactly `columns` for any table."""
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [(c,) for c in columns]

    class _Conn:
        def cursor(self):
            return _Cursor()

    monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})


def test_disabled_by_default(settings, monkeypatch):
    """Off means off — no introspection, nothing deferred."""
    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = False

    def _explode():
        raise AssertionError('must not introspect while disabled')

    monkeypatch.setattr(db_compat, 'connections', property(lambda self: _explode()))
    assert db_compat.missing_columns(Trial, 'trials') == ()


def test_reports_columns_the_database_lacks(settings, monkeypatch):
    every_column = [f.column for f in Trial._meta.local_fields]
    absent = 'omop_intervention_concept_ids'
    assert absent in every_column, 'fixture assumes this field exists on Trial'

    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
    _stub_columns(monkeypatch, [c for c in every_column if c != absent])

    assert db_compat.missing_columns(Trial, 'trials') == (absent,)


def test_reports_nothing_when_schema_matches(settings, monkeypatch):
    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
    _stub_columns(monkeypatch, [f.column for f in Trial._meta.local_fields])

    assert db_compat.missing_columns(Trial, 'trials') == ()


def test_missing_table_defers_nothing(settings, monkeypatch):
    """A missing table is a real gap, not something to paper over.

    Deferring every column would turn "this relation does not exist" into a
    query that still fails, but later and less clearly.
    """
    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
    _stub_columns(monkeypatch, [])

    assert db_compat.missing_columns(Trial, 'trials') == ()


def test_introspection_failure_defers_nothing(settings, monkeypatch):
    """If we cannot tell, we do not guess."""
    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True

    class _Conn:
        def cursor(self):
            raise RuntimeError('connection is down')

    monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
    assert db_compat.missing_columns(Trial, 'trials') == ()


def test_result_is_cached(settings, monkeypatch):
    """Schema does not change under a running process; introspect once."""
    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
    calls = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            calls.append(params)

        def fetchall(self):
            return [(f.column,) for f in Trial._meta.local_fields]

    class _Conn:
        def cursor(self):
            return _Cursor()

    monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
    db_compat.missing_columns(Trial, 'trials')
    db_compat.missing_columns(Trial, 'trials')
    assert len(calls) == 1


@pytest.mark.django_db
def test_defer_missing_columns_is_a_no_op_on_a_matching_database(settings):
    """The default path must not change the SQL at all."""
    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = False
    qs = Trial.objects.all()
    assert db_compat.defer_missing_columns(qs).query.deferred_loading == qs.query.deferred_loading
