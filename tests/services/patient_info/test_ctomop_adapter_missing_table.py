"""The legacy trial corpus can lack a whole reference table.

`TRIALS_DB_TOLERATE_MISSING_COLUMNS` defers absent columns, but the ORM still
raises `ProgrammingError` for a missing table. The normalisation endpoint must
resolve those values as unknown instead of returning a 500.
"""
import pytest
from django.db import ProgrammingError

from trials.services.patient_info import ctomop_adapter
from trials.services.patient_info.ctomop_adapter import _catalog_rows


class _Rows:
    def __init__(self, error):
        self.error = error

    def __iter__(self):
        raise self.error


class _Manager:
    def __init__(self, error):
        self.error = error

    def values(self, *args, **kwargs):
        return _Rows(self.error)


class _Meta:
    db_table = 'trials_marker'


class _Model:
    def __init__(self, error):
        self.objects = _Manager(error)
        self._meta = _Meta()


class _PgError(Exception):
    def __init__(self, pgcode):
        self.pgcode = pgcode


def _programming_error(pgcode):
    error = ProgrammingError('boom')
    error.__cause__ = _PgError(pgcode)
    return error


def test_missing_table_resolves_as_empty_catalog():
    assert _catalog_rows(_Model(_programming_error('42P01'))) == []


def test_missing_column_resolves_as_empty_catalog():
    assert _catalog_rows(_Model(_programming_error('42703'))) == []


def test_other_database_errors_still_fail_loudly():
    with pytest.raises(ProgrammingError):
        _catalog_rows(_Model(_programming_error('42501')))


@pytest.mark.django_db
def test_build_code_lookup_keeps_other_catalogs_when_one_model_is_missing(
    monkeypatch,
):
    """The table errors while the QuerySet is iterated, not while it is built."""
    ctomop_adapter._build_code_lookup.cache_clear()
    monkeypatch.setattr(
        'trials.models.Marker.objects',
        _Manager(_programming_error('42P01')),
    )
    try:
        lookup = ctomop_adapter._build_code_lookup()
    finally:
        ctomop_adapter._build_code_lookup.cache_clear()

    assert lookup['Marker'] == {}
    assert isinstance(lookup['Therapy'], dict)
    assert isinstance(lookup['_therapy'], dict)
