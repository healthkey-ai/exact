"""Tests for the stale-corpus tolerance in trials.db_compat.

The situation being modelled is a trials database whose schema predates the
models — see exact#360. It cannot be reproduced by dropping a column from the
test database, because the test database is built from the migrations that add
those columns. So introspection is stubbed instead, and the assertions are
about the decision the module makes given what it finds.
"""
import pytest

from django.db.utils import ProgrammingError

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


def test_install_is_a_no_op_when_disabled(settings):
    """Managers must be left alone entirely when the feature is off."""
    from django.apps import apps

    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = False
    config = apps.get_app_config('trials')
    db_compat.install(config)

    patched = [
        m for model in config.get_models()
        for m in model._meta.managers
        if getattr(m, '_defers_missing_columns', False)
    ]
    assert patched == []


def test_install_patches_each_manager_once(settings):
    """Idempotent: app registries can be re-readied in tests and reloads."""
    from django.apps import apps

    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
    config = apps.get_app_config('trials')
    try:
        db_compat.install(config)
        first = {
            id(m): m.get_queryset for model in config.get_models()
            for m in model._meta.managers
        }
        db_compat.install(config)
        second = {
            id(m): m.get_queryset for model in config.get_models()
            for m in model._meta.managers
        }
        assert first.keys() == second.keys()
        assert all(
            first[k].__func__ is second[k].__func__ for k in first
        ), 'second install re-wrapped an already-wrapped manager'
    finally:
        # Leave the registry as we found it — other tests share it.
        for model in config.get_models():
            for m in model._meta.managers:
                if getattr(m, '_defers_missing_columns', False):
                    del m.get_queryset
                    del m._defers_missing_columns


@pytest.mark.django_db
def test_details_skips_columns_the_database_lacks(settings, monkeypatch):
    """A corpus behind the models must not 500 the eligibility table.

    `TrialAttributes.details` walks every field the model declares and reads
    it. Against a database missing one of those columns, the deferred load
    fires on attribute access and raises ProgrammingError — which is how
    `POST /trials/<id>/match/` returned 500 on a deployment reading the legacy
    corpus (#360). The attribute should be skipped instead: the corpus holds
    no value for it, so there is nothing to render.
    """
    from trials.services.trial_details import trial_attributes as ta

    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
    absent = "omop_intervention_concept_ids"
    assert absent in [f.name for f in Trial._meta.fields]

    monkeypatch.setattr(
        ta, "missing_columns", lambda model, using: (absent,) if model is Trial else ()
    )

    trial = Trial.objects.create(
        study_id="NCT-TEST-0001", brief_title="t", disease="breast cancer"
    )

    # Reading it must be what would blow up, so make the attribute raise the
    # way a missing column does. `__set__` is what makes this a *data*
    # descriptor — without it the instance __dict__ shadows the class
    # attribute and nothing raises, which is exactly how Django's own
    # DeferredAttribute works.
    class _Boom:
        def __get__(self, obj, owner=None):
            raise ProgrammingError(f"column trials_trial.{absent} does not exist")

        def __set__(self, obj, value):
            pass

    monkeypatch.setattr(Trial, absent, _Boom(), raising=False)

    with pytest.raises(ProgrammingError):
        getattr(trial, absent)

    # A real PatientInfo, not None: `details` dereferences it, which is a
    # separate gap tracked in #362 and not what this test is about.
    from trials.services.patient_info.patient_info import PatientInfo

    out = ta.TrialAttributes(
        trial=trial, patient_info=PatientInfo(disease="breast cancer")
    ).details()
    assert absent not in out
