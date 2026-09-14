"""Tests for the stale-corpus tolerance in trials.db_compat.

The situation being modelled is a trials corpus whose schema predates the models
— see exact#360.

Most of these stub introspection and assert on the *decision* the module makes.
Two do not, and they are the load-bearing ones: `TestAgainstARealDriftedSchema`
drops a column inside the test transaction and exercises real introspection and
a real deferral against it. An earlier version of this file asserted only
decisions, and the whole suite stayed green with `defer_missing_columns` reduced
to `return queryset` — the mechanism the module exists for was untested.
"""
import time

import pytest

from django.db import connection
from django.db.utils import OperationalError, ProgrammingError

from trials import db_compat
from trials.models import Disease, Trial

ABSENT = 'omop_intervention_concept_ids'


@pytest.fixture(autouse=True)
def clear_cache():
    db_compat._cache.clear()
    db_compat._failed_at.clear()
    yield
    db_compat._cache.clear()
    db_compat._failed_at.clear()


@pytest.fixture
def corpus(settings):
    """Arm the feature with a corpus alias that exists.

    The real alias name, not an invented one: `TrialsDatabaseRouter` keys off
    `'trials'` specifically, so under any other name the router would still send
    Trial to `default` and every deferral here would be inert — a test that
    passes whatever the code does.

    Both halves are required: the flag alone does nothing unless a corpus is
    configured, which is the point of `TestRefusesWithoutACorpus`.
    """
    settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
    settings.DATABASES = {
        **settings.DATABASES,
        # A different NAME, not a copy of `default`: a corpus that resolves to
        # the primary database is refused, so a copy would leave every test
        # under this fixture inert. Nothing ever connects to it, though not
        # because every test stubs introspection — overriding `DATABASES` does
        # not reach the cached ConnectionHandler.settings, so an unstubbed
        # lookup raises ConnectionDoesNotExist and is handled as "cannot tell".
        'trials': {**settings.DATABASES['default'], 'NAME': 'exact_corpus_stub'},
    }
    return settings


def _clock_ahead(offset):
    """A `time` stand-in whose monotonic clock is *offset* seconds ahead."""
    real_monotonic = time.monotonic

    class _Clock:
        @staticmethod
        def monotonic():
            return real_monotonic() + offset

    return _Clock


def _stub_columns(monkeypatch, columns, alias='trials'):
    """Make introspection report exactly `columns` for the model's table."""
    class _Introspection:
        def table_names(self, cursor, include_views=False):
            return [Trial._meta.db_table]

        def get_table_description(self, cursor, table):
            return [type('Col', (), {'name': c})() for c in columns]

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Conn:
        introspection = _Introspection()

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr(db_compat, 'connections', {alias: _Conn()})


@pytest.fixture
def restore_managers():
    """Manager classes are process-global; put them back.

    Any test that calls `install()` needs this — without it, a gate that
    regresses leaves 56 models permanently patched and turns one failure into
    cascading ones in every test that runs afterwards.
    """
    from django.apps import apps

    config = apps.get_app_config('trials')
    original = [
        (m, type(m))
        for model in config.get_models()
        for m in model._meta.local_managers
    ]
    yield config
    for manager, cls in original:
        manager.__class__ = cls
    for model in config.get_models():
        model._meta._expire_cache()


class TestTheDecision:
    def test_reports_columns_the_database_lacks(self, corpus, monkeypatch):
        every_column = [f.column for f in Trial._meta.local_fields]
        assert ABSENT in every_column, 'fixture assumes this field exists on Trial'
        _stub_columns(monkeypatch, [c for c in every_column if c != ABSENT])

        assert db_compat.missing_columns(Trial, 'trials') == (ABSENT,)

    def test_reports_nothing_when_schema_matches(self, corpus, monkeypatch):
        _stub_columns(monkeypatch, [f.column for f in Trial._meta.local_fields])
        assert db_compat.missing_columns(Trial, 'trials') == ()

    def test_missing_table_defers_nothing(self, corpus, monkeypatch):
        """A missing table is a real gap, not something to paper over.

        Deferring every column would turn "this relation does not exist" into a
        query that still fails, but later and less clearly.
        """
        described = []

        class _Introspection:
            def table_names(self, cursor, include_views=False):
                return []

            def get_table_description(self, cursor, table):
                # Returns nothing rather than raising: an exception here would
                # be swallowed by the module's own `except Exception` and
                # produce the same `()` this test asserts, so removing the
                # missing-table branch would go unnoticed.
                described.append(table)
                return []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            introspection = _Introspection()

            def cursor(self):
                return _Cursor()

        monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
        assert db_compat.missing_columns(Trial, 'trials') == ()
        assert described == [], 'described a table it had not found'

    def test_introspection_failure_defers_nothing(self, corpus, monkeypatch):
        """If we cannot tell, we do not guess."""
        class _Conn:
            def cursor(self):
                raise OperationalError('connection is down')

        monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
        assert db_compat.missing_columns(Trial, 'trials') == ()

    def test_introspection_failure_is_not_retried_per_queryset(
        self, corpus, monkeypatch
    ):
        """Every queryset in the app runs through this; an uncached failure
        would mean one connection attempt and one traceback each."""
        attempts = []

        class _Conn:
            def cursor(self):
                attempts.append(1)
                raise OperationalError('connection is down')

        monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
        for _ in range(5):
            assert db_compat.missing_columns(Trial, 'trials') == ()
        assert len(attempts) == 1

    def test_introspection_is_retried_after_the_interval(
        self, corpus, monkeypatch
    ):
        """A blip must not disable the tolerance for the worker's lifetime.

        Remembering a failure forever would mean every later query against a
        drifted corpus fails on the missing column until a restart.
        """
        attempts = []
        working = {'yes': False}

        class _Introspection:
            def table_names(self, cursor, include_views=False):
                return [Trial._meta.db_table]

            def get_table_description(self, cursor, table):
                return [
                    type('Col', (), {'name': f.column})()
                    for f in Trial._meta.local_fields
                    if f.column != ABSENT
                ]

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            introspection = _Introspection()

            def cursor(self):
                attempts.append(1)
                if not working['yes']:
                    raise OperationalError('connection is down')
                return _Cursor()

        monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
        assert db_compat.missing_columns(Trial, 'trials') == ()
        assert len(attempts) == 1

        working['yes'] = True
        # Replace the module's reference to `time`, not `time.monotonic` itself:
        # patching the real module is process-wide for the duration and reaches
        # anything else that happens to read the clock during the test.
        monkeypatch.setattr(
            db_compat, 'time', _clock_ahead(db_compat.INTROSPECTION_RETRY_INTERVAL + 1)
        )
        assert db_compat.missing_columns(Trial, 'trials') == (ABSENT,)
        assert len(attempts) == 2

    def test_result_is_cached(self, corpus, monkeypatch):
        """Schema does not change under a running process; introspect once."""
        calls = []

        class _Introspection:
            def table_names(self, cursor, include_views=False):
                calls.append('table_names')
                return [Trial._meta.db_table]

            def get_table_description(self, cursor, table):
                return [
                    type('Col', (), {'name': f.column})()
                    for f in Trial._meta.local_fields
                ]

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            introspection = _Introspection()

            def cursor(self):
                return _Cursor()

        monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
        db_compat.missing_columns(Trial, 'trials')
        db_compat.missing_columns(Trial, 'trials')
        assert len(calls) == 1

    def test_unsaved_instance_alias_is_not_introspected(self, settings, monkeypatch):
        """`instance._state.db` is None before a save; trial_attributes passes
        it straight through.

        No corpus configured, which is where the `not using` guard is actually
        load-bearing: with one, `None != 'trials'` returns early anyway and this
        would pass with the guard deleted.
        """
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
        assert 'trials' not in settings.DATABASES
        reached = []
        monkeypatch.setattr(
            db_compat, 'connections', _Explodes(lambda: reached.append(1))
        )
        assert db_compat.missing_columns(Trial, None) == ()
        assert reached == []


class _Explodes:
    """A `connections` stand-in that fails on any subscript.

    A mapping, not a `property` object: subscripting a property raises
    TypeError, which the module's own `except Exception` swallows into the same
    `()` the test asserts — so the assertion would hold even if the guard under
    test were deleted.
    """

    def __init__(self, boom):
        self._boom = boom

    def __getitem__(self, key):
        self._boom()


class TestOffByDefault:
    """The off-switch is the feature's whole safety argument, so it is asserted
    by observing that introspection never happens — not by expecting an
    exception the module catches and converts into the same empty answer."""

    def test_flag_off_does_not_introspect(self, settings, monkeypatch):
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = False
        settings.DATABASES = {
            **settings.DATABASES,
            'trials': {**settings.DATABASES['default'], 'NAME': 'exact_corpus_stub'},
        }
        reached = []
        monkeypatch.setattr(
            db_compat, 'connections', _Explodes(lambda: reached.append(1))
        )

        assert db_compat.missing_columns(Trial, 'trials') == ()
        assert reached == [], 'introspected while disabled'

    def test_install_is_a_no_op_when_disabled(self, settings, restore_managers):
        """Managers must be left alone entirely when the feature is off.

        A corpus alias is configured deliberately: without one, `install()`
        returns on the alias gate whatever the flag says, and this test would
        pass with the off-switch deleted — which is exactly how the flag's own
        test used to be inert.
        """
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = False
        settings.DATABASES = {
            **settings.DATABASES,
            'trials': {**settings.DATABASES['default'], 'NAME': 'exact_corpus_stub'},
        }
        config = restore_managers
        db_compat.install(config)

        patched = [
            m for model in config.get_models()
            for m in model._meta.managers
            if getattr(m, '_defers_missing_columns', False)
        ]
        assert patched == []

    @pytest.mark.django_db
    def test_defer_is_a_no_op_when_the_flag_is_off(self, settings, monkeypatch):
        """Drift present, corpus armed — only the flag says no.

        Both halves matter, and getting either wrong is why this test could not
        fail for three review rounds. Without configured drift the deferral is
        empty on the merits; without a corpus the alias gate returns before the
        flag is read at all. The earlier version patched `corpus_alias` and
        `MIGRATIONS_TABLE`, which are never reached when the flag is off, and ran
        against a database that matches the models.
        """
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = False
        settings.DATABASES = {
            **settings.DATABASES,
            'trials': {**settings.DATABASES['default'], 'NAME': 'exact_corpus_stub'},
        }
        _stub_columns(
            monkeypatch,
            [f.column for f in Trial._meta.local_fields if f.column != ABSENT],
        )
        qs = Trial.objects.using('trials')
        assert db_compat.defer_missing_columns(qs).query.deferred_loading == (
            frozenset(), True
        ), 'deferred with the flag off'


class TestAViewBackedCorpus:
    """A legacy schema published under Django's table names is very plausibly
    exposed as views, and `table_names()` excludes those by default."""

    def test_a_relation_visible_only_as_a_view_is_not_read_as_absent(
        self, corpus, monkeypatch
    ):
        asked = []

        class _Introspection:
            def table_names(self, cursor, include_views=False):
                asked.append(include_views)
                # What Postgres reports: relkind 'v'/'m' are typed 'v', which
                # the base implementation drops unless views are requested.
                return [Trial._meta.db_table] if include_views else []

            def get_table_description(self, cursor, table):
                return [
                    type('Col', (), {'name': f.column})()
                    for f in Trial._meta.local_fields
                    if f.column != ABSENT
                ]

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            introspection = _Introspection()

            def cursor(self):
                return _Cursor()

        monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
        assert db_compat.missing_columns(Trial, 'trials') == (ABSENT,), \
            'read a view-backed relation as an absent table'
        assert asked == [True], f'did not ask for views: include_views={asked}'


class TestWhichDatabaseAConfigAddresses:
    """`corpus_alias()` refuses a corpus that is really the primary database, so
    what counts as "the same database" decides both whether the guard holds and
    whether the feature works at all where it is needed.

    Every row here was wrong under a raw comparison of HOST/PORT/NAME: the first
    two left the guard open in the likeliest typo configs, the last disabled the
    feature outright on Cloud SQL.
    """

    PRIMARY = {'HOST': '127.0.0.1', 'PORT': '5432', 'NAME': 'exact'}

    @pytest.mark.parametrize('corpus_config,same,why', [
        ({'HOST': '127.0.0.1', 'PORT': '5432', 'NAME': 'exact'},
         True, 'identical'),
        ({'HOST': '127.0.0.1', 'NAME': 'exact'},
         True, 'no port stated, and 5432 is the default'),
        ({'HOST': 'localhost', 'PORT': '5432', 'NAME': 'exact'},
         True, 'localhost is 127.0.0.1, and is this project default HOST'),
        ({'HOST': 'LOCALHOST', 'PORT': '5432', 'NAME': 'exact'},
         True, 'hostnames are case-insensitive'),
        ({'HOST': 'localhost.', 'PORT': '5432', 'NAME': 'exact'},
         True, 'a fully-qualified root dot is the same host'),
        ({'HOST': 'CORPUS.INTERNAL', 'PORT': '5432', 'NAME': 'exact'},
         False, 'folding case must not make a different host match'),
        ({'HOST': '', 'PORT': '', 'NAME': 'exact',
          'OPTIONS': {'host': '/cloudsql/inst'}},
         False, 'a unix socket is not the TCP primary'),
        ({'HOST': 'corpus.internal', 'PORT': '5432', 'NAME': 'exact'},
         False, 'same name on another host is another database'),
        ({'HOST': '127.0.0.1', 'PORT': '5433', 'NAME': 'exact'},
         False, 'another port is another server'),
        ({'HOST': '127.0.0.1', 'PORT': '5432', 'NAME': 'exact_corpus'},
         False, 'another name on the same server'),
    ])
    def test_same_database_detection(self, corpus_config, same, why):
        assert db_compat._same_database(corpus_config, self.PRIMARY) is same, why

    def test_two_cloudsql_instances_are_not_the_same_database(self):
        """dj_database_url leaves HOST empty and puts the socket in OPTIONS, so
        on HOST and NAME alone two different instances look identical — which
        would refuse a corpus that legitimately needs the feature."""
        app = {'HOST': '', 'PORT': '', 'NAME': 'exact',
               'OPTIONS': {'host': '/cloudsql/app-inst'}}
        corpus = {'HOST': '', 'PORT': '', 'NAME': 'exact',
                  'OPTIONS': {'host': '/cloudsql/corpus-inst'}}
        assert db_compat._same_database(corpus, app) is False
        assert db_compat._same_database(corpus, corpus) is True

    @pytest.mark.parametrize('corpus_config,expected', [
        ({'HOST': '127.0.0.1', 'NAME': 'exact'}, None),
        ({'HOST': 'localhost', 'PORT': '5432', 'NAME': 'exact'}, None),
        ({'HOST': 'corpus.internal', 'PORT': '5432', 'NAME': 'exact'}, 'trials'),
    ])
    def test_corpus_alias_follows_it(self, settings, corpus_config, expected):
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
        settings.DATABASES = {
            'default': {**settings.DATABASES['default'], **self.PRIMARY},
            'trials': corpus_config,
        }
        assert db_compat.corpus_alias() == expected


class TestTheMigrationsBackstop:
    """The one check that does not depend on how a host is spelled.

    A corpus is defined by having no `django_migrations` table. Comparing
    DATABASES entries can only enumerate spellings of "the same host" — a socket
    directory, a hostname against its IP, a different case — and every spelling
    it misses arms the tolerance on a database whose drift is an un-applied
    migration.
    """

    def _conn(self, tables, described=None):
        class _Introspection:
            def table_names(self, cursor, include_views=False):
                return tables

            def get_table_description(self, cursor, table):
                if described is not None:
                    described.append(table)
                return [
                    type('Col', (), {'name': f.column})()
                    for f in Trial._meta.local_fields
                    if f.column != ABSENT
                ]

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            introspection = _Introspection()

            def cursor(self):
                return _Cursor()

        return _Conn()

    def test_a_database_this_project_migrates_is_refused(self, corpus, monkeypatch):
        described = []
        monkeypatch.setattr(db_compat, 'connections', {'trials': self._conn(
            [Trial._meta.db_table, 'django_migrations'], described)})

        assert db_compat.missing_columns(Trial, 'trials') == ()
        assert described == [], 'described a table it should not have looked at'

    def test_a_corpus_without_one_is_accepted(self, corpus, monkeypatch):
        monkeypatch.setattr(db_compat, 'connections', {'trials': self._conn(
            [Trial._meta.db_table])})

        assert db_compat.missing_columns(Trial, 'trials') == (ABSENT,)

    def test_the_refusal_is_remembered(self, corpus, monkeypatch):
        """It is a fact about the database, not a failure to retry."""
        calls = []

        class _Introspection:
            def table_names(self, cursor, include_views=False):
                calls.append(1)
                return [Trial._meta.db_table, 'django_migrations']

            def get_table_description(self, cursor, table):
                raise AssertionError('unreachable')

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            introspection = _Introspection()

            def cursor(self):
                return _Cursor()

        monkeypatch.setattr(db_compat, 'connections', {'trials': _Conn()})
        for _ in range(4):
            assert db_compat.missing_columns(Trial, 'trials') == ()
        assert len(calls) == 1


class TestRefusesWithoutACorpus:
    """The flag must never defer against the primary database.

    The router sends trials models to `default` when TRIALS_DATABASE_URL is
    unset. Without this restriction the flag would read an un-applied migration
    on the app's own database as external drift — and, together with the
    `details()` skip, answer 200 with a short eligibility table where it used to
    raise. An operator who forgot `migrate` would see green.
    """

    def test_no_corpus_alias_defers_nothing(self, settings, monkeypatch):
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
        assert 'trials' not in settings.DATABASES, 'fixture assumes a single database'
        reached = []
        monkeypatch.setattr(
            db_compat, 'connections', _Explodes(lambda: reached.append(1))
        )

        assert db_compat.missing_columns(Trial, 'default') == ()
        assert reached == [], 'introspected the primary database'

    def test_the_primary_alias_is_never_introspected(self, corpus, monkeypatch):
        """Even with a corpus configured, `default` is out of scope."""
        reached = []
        monkeypatch.setattr(
            db_compat, 'connections', _Explodes(lambda: reached.append(1))
        )

        assert db_compat.missing_columns(Trial, 'default') == ()
        assert reached == []

    def test_a_corpus_that_is_really_the_primary_database_is_refused(
        self, settings, monkeypatch
    ):
        """Pointing TRIALS_DATABASE_URL at the same host, port and name as
        `default` is a plausible way to run the split-database code path against
        one database — and it walks straight through a bare alias check, because
        the alias exists. The drift found there is an un-applied migration.
        """
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
        settings.DATABASES = {
            **settings.DATABASES,
            'trials': dict(settings.DATABASES['default']),
        }
        reached = []
        monkeypatch.setattr(
            db_compat, 'connections', _Explodes(lambda: reached.append(1))
        )

        assert db_compat.corpus_alias() is None
        assert db_compat.missing_columns(Trial, 'trials') == ()
        assert reached == [], 'introspected a corpus that is the primary database'

    def test_a_corpus_on_another_host_with_the_same_name_is_accepted(
        self, settings
    ):
        """The name alone must not decide it: two servers' `exact` databases are
        different databases, and refusing that would disable the feature for a
        deployment that legitimately needs it."""
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
        settings.DATABASES = {
            **settings.DATABASES,
            'trials': {
                **settings.DATABASES['default'],
                'HOST': 'corpus.internal',
            },
        }
        assert db_compat.corpus_alias() == 'trials'

    def test_install_refuses_to_arm(self, settings, restore_managers):
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
        assert 'trials' not in settings.DATABASES
        config = restore_managers
        db_compat.install(config)

        patched = [
            m for model in config.get_models()
            for m in model._meta.managers
            if getattr(m, '_defers_missing_columns', False)
        ]
        assert patched == [], 'armed without a corpus to be tolerant of'


class TestInstall:

    def test_patches_the_default_manager(self, corpus, restore_managers):
        config = restore_managers
        db_compat.install(config)
        assert Trial.objects._defers_missing_columns is True

    def test_is_idempotent(self, corpus, restore_managers):
        """App registries get re-readied in tests and on reload."""
        config = restore_managers
        db_compat.install(config)
        first = {
            model: type(m)
            for model in config.get_models()
            for m in model._meta.local_managers
        }
        db_compat.install(config)
        second = {
            model: type(m)
            for model in config.get_models()
            for m in model._meta.local_managers
        }
        assert first == second, 'a second install wrapped an already-wrapped manager'

    def test_survives_a_registry_cache_clear(self, corpus, restore_managers):
        """`Options.managers` is a cached_property holding copies, so a patch
        applied to those copies disappears when the registry is cleared."""
        from django.apps import apps

        config = restore_managers
        db_compat.install(config)
        apps.clear_cache()
        assert Trial.objects._defers_missing_columns is True

    def test_manager_using_decides_against_the_target_alias(
        self, corpus, restore_managers, monkeypatch
    ):
        """`Manager.using` is a passthrough to `get_queryset().using(alias)`.

        Undefended, the deferral is chosen for the router's alias and then the
        query is moved elsewhere — carrying corpus deferrals onto `default` and
        dropping columns that database really has.
        """
        config = restore_managers
        db_compat.install(config)
        asked = []

        def fake_missing(model, using):
            asked.append(using)
            return (ABSENT,) if using == 'trials' else ()

        monkeypatch.setattr(db_compat, 'missing_columns', fake_missing)

        qs = Trial.objects.using('default')
        assert qs.db == 'default'
        assert qs.query.deferred_loading == (frozenset(), True), \
            'carried a corpus deferral onto the primary database'
        assert 'default' in asked

    def test_db_manager_keeps_its_alias(self, corpus, restore_managers):
        """`db_manager` copies the manager and sets `_db` on the copy, so a
        method bound to the original would silently ignore the requested
        alias and query whatever the router picked."""
        config = restore_managers
        db_compat.install(config)
        assert Trial.objects._defers_missing_columns is True, 'nothing was patched'
        # 'default', not 'trials': the router already sends Trial to the corpus,
        # so asserting 'trials' holds even when the copied manager's `_db` is
        # ignored. Only the alias the router would *not* have chosen shows that
        # the requested one survived.
        assert Trial.objects.db_manager('default').get_queryset().db == 'default'
        assert Trial.objects.db_manager('trials').get_queryset().db == 'trials'


class TestThePatchedManagersStayUsable:
    """Replacing a manager's class is invasive. These cover the mechanisms that
    resolve a manager class *by name* — and so break silently on a dynamically
    created one — plus what `install()` must do to the registry."""

    def test_managers_still_pickle(self, corpus, restore_managers):
        """Pickling looks the class up in the module it names. Before the class
        was registered there, this raised PicklingError."""
        import pickle

        db_compat.install(restore_managers)
        for model in (Trial, Disease):
            restored = pickle.loads(pickle.dumps(model.objects))
            assert type(restored) is type(model.objects)

    def test_managers_still_deconstruct(self, corpus, restore_managers):
        """`BaseManager.deconstruct` imports `__module__` and raises if the name
        is absent. Via makemigrations that would surface only for a manager with
        `use_in_migrations = True`, and no trials manager sets one — so this
        asserts deconstruct directly instead."""
        db_compat.install(restore_managers)
        assert Trial.objects._defers_missing_columns is True, 'nothing was patched'
        for model in (Trial, Disease):
            assert model.objects.deconstruct()

    def test_managers_built_before_install_are_replaced(
        self, corpus, restore_managers
    ):
        """`Options.managers` is a cached_property holding copies of
        `local_managers`. Patching the originals does nothing for copies already
        handed out, so `install()` expires the cache — without that, anything
        that touched a manager before app loading finished keeps the unpatched
        one."""
        config = restore_managers
        # Populate the cache with pre-patch copies, the way an import that
        # touches a manager during app loading would.
        assert Trial._meta.managers  # populating it is the point
        assert 'managers' in Trial._meta.__dict__, 'premise: copies cached pre-patch'
        assert getattr(Trial.objects, '_defers_missing_columns', False) is False

        db_compat.install(config)
        assert Trial.objects._defers_missing_columns is True

    def test_the_base_manager_is_not_covered(self, corpus, restore_managers):
        """`Options.base_manager` builds a fresh plain Manager, so forward FK
        dereference is *not* covered — the limits list says so and this pins it,
        because the related-manager result above invites assuming otherwise."""
        db_compat.install(restore_managers)
        assert Trial.objects._defers_missing_columns is True, 'nothing was patched'
        assert getattr(Trial._base_manager, '_defers_missing_columns', False) is False


@pytest.mark.django_db
class TestAgainstARealDriftedSchema:
    """Real DDL, real introspection, real deferral.

    The column is dropped inside the test transaction and rolled back with it.
    `corpus_alias` is pointed at this database for the duration, which is the
    only way to exercise the corpus path against a schema the test controls —
    two aliases onto one database would not share the transaction, so the drop
    would be invisible to the introspecting connection.
    """

    @pytest.fixture
    def drifted(self, settings, monkeypatch):
        """One row, then the column dropped from under it.

        The row is written first because an INSERT names every local field, so
        `Trial.objects.create()` cannot work against the drifted table — which
        is correct and out of scope: this tolerance is for a read-only consumer.
        """
        Trial.objects.create(
            study_id='NCT-DRIFT-0001', brief_title='keep', disease='breast cancer'
        )
        with connection.cursor() as cursor:
            # The INSERT above queued deferred FK trigger events, and Postgres
            # refuses to ALTER a table that has any. Firing them now empties the
            # queue; they would have fired at commit anyway.
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
            cursor.execute(
                f'ALTER TABLE {Trial._meta.db_table} DROP COLUMN {ABSENT}'
            )
        settings.TRIALS_DB_TOLERATE_MISSING_COLUMNS = True
        # Says the premise outright — "treat this database as the corpus" —
        # rather than leaning on a special case in `corpus_alias()`. Production
        # code should not carry an exemption that exists for a test.
        monkeypatch.setattr(db_compat, 'corpus_alias', lambda: 'default')
        # ...and that it has no migrations table. It is this project's own test
        # database, so it does; pointing the backstop at a name that is absent
        # is how the premise gets stated rather than worked around.
        monkeypatch.setattr(db_compat, 'MIGRATIONS_TABLE', 'not_a_table_here')
        db_compat._cache.clear()
        return ABSENT

    def _deferred(self):
        return db_compat.defer_missing_columns(Trial.objects.all())

    def test_introspection_finds_the_dropped_column(self, drifted):
        assert db_compat.missing_columns(Trial, 'default') == (drifted,)

    def test_an_undeferred_fetch_raises(self, drifted):
        """Establishes that the drift is real, so the next test means something."""
        with pytest.raises(ProgrammingError):
            list(Trial.objects.all().only(drifted))

    def test_a_deferred_fetch_succeeds(self, drifted):
        """The whole point: the endpoint's query works again."""
        rows = list(self._deferred().filter(study_id='NCT-DRIFT-0001'))
        assert [t.study_id for t in rows] == ['NCT-DRIFT-0001']

    def test_the_dropped_column_leaves_the_select(self, drifted):
        sql = str(self._deferred().query)
        assert drifted not in sql
        assert 'study_id' in sql, 'deferred the column, and everything else too'

    def test_reading_the_column_still_raises(self, drifted):
        """Deferral hides the column from SELECT; it does not invent a value."""
        trial = self._deferred()[0]
        with pytest.raises(ProgrammingError):
            getattr(trial, drifted)

    def test_filtering_on_the_column_still_raises(self, drifted):
        """`?therapy_id=` reaches exactly this, and deferral cannot help it."""
        with pytest.raises(ProgrammingError):
            list(self._deferred().filter(**{f'{drifted}__contains': [1]}))

    def test_values_on_the_column_still_raises(self, drifted):
        """`values()` calls clear_deferred_loading(), so it re-selects."""
        with pytest.raises(ProgrammingError):
            list(self._deferred().values(drifted))

    def test_an_untouched_column_is_unaffected(self, drifted):
        qs = self._deferred()
        # `count()` selects no columns, so on its own this passes even against a
        # table missing every one of them — assert something was deferred first.
        assert qs.query.deferred_loading[0], 'nothing was deferred'
        assert qs.filter(brief_title='keep').count() == 1


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
    assert ABSENT in [f.name for f in Trial._meta.fields]

    monkeypatch.setattr(
        ta, 'missing_columns', lambda model, using: (ABSENT,) if model is Trial else ()
    )

    trial = Trial.objects.create(
        study_id='NCT-TEST-0001', brief_title='t', disease='breast cancer'
    )

    # Reading it must be what would blow up, so make the attribute raise the
    # way a missing column does. `__set__` is what makes this a *data*
    # descriptor — without it the instance __dict__ shadows the class
    # attribute and nothing raises, which is exactly how Django's own
    # DeferredAttribute works.
    class _Boom:
        def __get__(self, obj, owner=None):
            raise ProgrammingError(f'column trials_trial.{ABSENT} does not exist')

        def __set__(self, obj, value):
            pass

    monkeypatch.setattr(Trial, ABSENT, _Boom(), raising=False)

    with pytest.raises(ProgrammingError):
        getattr(trial, ABSENT)

    # A real PatientInfo, not None: `details` dereferences it, which is a
    # separate gap tracked in #362 and not what this test is about.
    from trials.services.patient_info.patient_info import PatientInfo

    # That this does not raise is the assertion: `details()` walks every declared
    # field and reads it, and the deferred load fires on access. Asserting
    # `ABSENT not in out` would be vacuous — that field is never rendered.
    out = ta.TrialAttributes(
        trial=trial, patient_info=PatientInfo(disease='breast cancer')
    ).details()
    assert out
