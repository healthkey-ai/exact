"""Tolerate a trials corpus whose schema predates the current models.

The trials corpus is an externally managed, data-only database: it carries no
``django_migrations`` table, and ``TrialsDatabaseRouter.allow_migrate`` refuses
to migrate the ``trials`` app precisely because its schema is somebody else's
to change. The consequence is that the models can move ahead of it and nothing
notices until a query names a column the corpus does not have, at which point
every trial endpoint 500s.

This module finds that gap at runtime so the endpoints can defer the columns
instead of failing on them. It is a stopgap, not a fix — see exact#360. The
honest fix is for the corpus and the models to be tied together; until they
are, a read-only consumer needs a way to work against what actually exists.

Deliberately narrow:

* **Only the corpus.** Nothing is ever deferred against ``default``. The router
  falls back to ``default`` for trials models when ``TRIALS_DATABASE_URL`` is
  unset, so without this restriction the flag would treat an un-applied
  migration on the app's own database as a stale external corpus — turning a
  loud ``ProgrammingError`` into a service that quietly answers with less data.
* Only columns are handled. A missing *table* means a relation the corpus has
  never held, and quietly serving empty results for it would hide a real gap.
* Deferred columns still raise if something reads them. Django loads a deferred
  attribute on access, which re-queries the missing column. That is the right
  outcome: a filter over data the corpus does not carry should fail loudly
  rather than silently return matches computed from nothing.
* Off unless ``TRIALS_DB_TOLERATE_MISSING_COLUMNS`` is set, so a deployment
  whose database does match the models behaves exactly as before.

What it does **not** cover, because deferral cannot:

* ``filter()`` / ``order_by()`` on an absent column. On ``main`` the live case
  is ``?therapy_id=``, which reaches ``TrialQuerySet.by_therapy_id`` and filters
  ``omop_intervention_concept_ids__contains`` — that request still fails against
  a corpus lacking the column, by design.
* ``values()`` / ``values_list()``, which call ``clear_deferred_loading()`` and
  so re-select whatever they name.
* ``Model._base_manager``. ``Options.base_manager`` builds a fresh ``Manager``
  when ``base_manager_name`` is unset, which it is for every trials model, so
  that manager is not wrapped. It is what Django uses for forward foreign-key
  dereference, ``refresh_from_db``, ``validate_unique`` and serialization — so
  following an FK into a drifted table still fails.
* ``select_related()``. The joined model's columns are composed from its own
  field list, which no manager-level deferral touches, so drift in a related
  table still fails. The app uses it on trials models — ``trials/api/
  trials_views.py`` and ``trials/services/value_options.py``.
* ``Model.objects.all().using(alias)`` — moving an *already built* queryset to
  another database keeps whatever was deferred for the one it was built for.
  ``Model.objects.using(alias)`` is handled, since that is the form the app
  uses; the queryset-level one is not reachable from anywhere here.

Each of those is a reason this is a stopgap and not an abstraction to build on.

Reverse and many-to-many related managers (``instance.related_set``,
``instance.m2m_field``) do inherit the deferral, incidentally: Django builds a
related manager by subclassing ``related_model._default_manager.__class__``,
which by then is the patched one. That holds because ``install()`` runs at
``ready()``, before anything touches a descriptor — the class is cached on first
access, so it is a consequence of ordering rather than a guarantee. Do not rely
on it. *Forward* FK dereference (``trial.disease``) is not covered either way,
because that path goes through ``_base_manager``.
"""
import logging
import time

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.utils import ConnectionDoesNotExist, DatabaseError, Error

from exact.db_router import _TRIALS_DB

logger = logging.getLogger(__name__)

#: The alias the externally-owned corpus is configured under — imported from
#: the router rather than restated, so the two cannot drift and leave this
#: tolerance covering an alias nothing routes to. Nothing reassigns it: a test
#: that needs this database treated as the corpus replaces ``corpus_alias``
#: instead, so no safety property here depends on the name it holds.
TRIALS_DB_ALIAS = _TRIALS_DB

#: Seconds before a failed introspection is attempted again. Bounds both
#: hazards, neither of which a plain cache gets right: remembered forever, one
#: blip while the corpus is unreachable disables the tolerance until the worker
#: restarts, and every later query fails on the missing column; not remembered
#: at all, an unreachable corpus costs a connection attempt and a full traceback
#: per queryset built anywhere in the app.
INTROSPECTION_RETRY_INTERVAL = 60

#: Presence of this table is what distinguishes a database this project migrates
#: from the externally owned corpus this tolerance is for.
MIGRATIONS_TABLE = 'django_migrations'

_cache = {}
_failed_at = {}
_deferring_classes = {}


def _database_identity(config):
    """What database a DATABASES entry actually addresses.

    A cheap pre-check, not the guarantee: host spellings are unbounded — a socket
    directory, a hostname against its IP, an alias in /etc/hosts — so this can
    only catch the common ones. What actually keeps the tolerance off a database
    this project migrates is the ``MIGRATIONS_TABLE`` backstop in
    ``missing_columns``, which asks the database instead of the config.

    Normalised, because comparing the raw entries gets every realistic case
    wrong. A URL without a port and one with an explicit 5432 are the same
    server; ``localhost`` and ``127.0.0.1`` are the same host, and ``localhost``
    is this project's default ``DATABASE_HOST``; and under the Cloud SQL socket
    form ``dj_database_url`` leaves ``HOST`` empty and puts the socket path in
    ``OPTIONS['host']``, so two *different* instances look identical on ``HOST``
    and ``NAME`` alone.
    """
    options = config.get('OPTIONS') or {}
    host = str(config.get('HOST') or options.get('host') or 'localhost').lower()
    host = host.rstrip('.')
    if host in ('localhost', '127.0.0.1', '::1'):
        host = 'localhost'
    port = str(config.get('PORT') or options.get('port') or '5432')
    name = str(config.get('NAME') or options.get('dbname') or '')
    return host, port, name


def _same_database(one, other):
    """Whether two DATABASES entries address the same physical database."""
    return _database_identity(one) == _database_identity(other)


def corpus_alias():
    """The alias the corpus lives on, or ``None`` when there is no corpus.

    ``None`` for the single-database case — the router sends trials models to
    ``default``, which this tolerance must never touch — and also when the
    corpus alias is configured but *resolves to* the primary database. Pointing
    ``TRIALS_DATABASE_URL`` at the same host, port and name as ``default`` is a
    plausible way to run the split-database code path against one database, and
    it would otherwise walk straight through the alias check: the drift found
    there is an un-applied migration on the app's own database, which is the
    thing this is not allowed to paper over.

    No special case for a corpus alias that *is* ``default``: the safety
    property should not rest on a convention about which name the constant
    holds. A test that needs this database treated as the corpus replaces this
    function instead.
    """
    alias = TRIALS_DB_ALIAS
    if alias not in settings.DATABASES:
        return None
    if _same_database(
        settings.DATABASES[alias], settings.DATABASES.get(DEFAULT_DB_ALIAS, {})
    ):
        return None
    return alias


def _enabled():
    return bool(getattr(settings, 'TRIALS_DB_TOLERATE_MISSING_COLUMNS', False))


def missing_columns(model, using):
    """Model field names whose columns are absent from the ``using`` database.

    Empty unless the feature is on, ``using`` is the corpus alias, the table is
    present, and introspection succeeded — every other case is one where
    deferring would guess rather than know.

    Answers are cached per (model, alias); the schema does not change inside a
    process. A *failure* is remembered only for ``INTROSPECTION_RETRY_INTERVAL``,
    so a transient outage does not disable the tolerance for the life of the
    worker.

    The asymmetry is deliberate but worth knowing: a *successful* answer is kept
    for the life of the process, so once the corpus catches up with the models,
    running workers keep deferring columns that now exist — serving incomplete
    data with no further log line — until they are restarted. Restart the
    workers after the corpus is migrated. Bounding this too would mean
    re-introspecting on a timer for a schema that almost never changes.
    """
    # Cheapest exit first: this runs for every queryset built in the app while
    # the flag is on. `using` is None for an unsaved instance, and 'default'
    # whenever no corpus is configured.
    if not _enabled():
        return ()
    if not using or using != corpus_alias():
        return ()

    key = (model._meta.label, using)
    if key in _cache:
        return _cache[key]
    last_failure = _failed_at.get(key)
    if (
        last_failure is not None
        and time.monotonic() - last_failure < INTROSPECTION_RETRY_INTERVAL
    ):
        return ()

    table = model._meta.db_table
    try:
        # Inside the try: resolving the alias can itself raise
        # ConnectionDoesNotExist, and that is exactly a case of "we cannot
        # tell", not a reason to propagate out of every queryset in the app.
        connection = connections[using]
        # Not savepointed, deliberately. If this ever ran inside a caller's
        # transaction, a failure here would leave it aborted and the caller's
        # next query would fail with an InFailedSqlTransaction naming none of
        # this — but wrapping it in `transaction.atomic(using=...)` reaches the
        # real connection registry, which is not worth the coupling while
        # nothing in `trials/` opens a transaction and ATOMIC_REQUESTS is off.
        with connection.cursor() as cursor:
            # The introspection API rather than a hand-written
            # information_schema query: it resolves the table through the
            # connection's search_path the same way the ORM does, so a corpus
            # whose role carries a non-default search_path is not misread as an
            # absent table.
            # include_views: a corpus exposed as views or materialized views
            # over a legacy schema is the normal way to publish one under
            # Django's table names, and the default here excludes them — which
            # would read a present relation as an absent table, cache that, and
            # 500 every endpoint with the flag armed.
            tables = connection.introspection.table_names(
                cursor, include_views=True
            )
            # The backstop, and the only config-independent one available: a
            # corpus is *defined* at the top of this module as a database with
            # no django_migrations table. Comparing DATABASES entries can only
            # ever enumerate spellings of "the same host" — a socket directory,
            # a hostname against its IP, a different case — and every spelling
            # it misses arms the tolerance on a database whose drift is an
            # un-applied migration. Asking the database costs nothing here,
            # since the table list is already in hand.
            if MIGRATIONS_TABLE in tables:
                logger.warning(
                    '%r has a %s table, so it is a database this project '
                    'migrates rather than an externally owned corpus. Deferring '
                    'nothing: drift there is an un-applied migration, and '
                    'papering over it would answer with less data instead of '
                    'failing. (A corpus that carries a stray %s cannot use this '
                    'tolerance.)',
                    using, MIGRATIONS_TABLE, MIGRATIONS_TABLE,
                )
                _failed_at.pop(key, None)
                _cache[key] = ()
                return ()
            if table not in tables:
                logger.warning(
                    '%s is not present on %r at all. Deferring nothing: a '
                    'missing table is a real gap, not a schema that drifted.',
                    table, using,
                )
                _failed_at.pop(key, None)
                _cache[key] = ()
                return ()
            present = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, table
                )
            }
    except (DatabaseError, Error, ConnectionDoesNotExist):
        logger.exception(
            'Could not introspect %s on %r; deferring nothing there for %ss',
            table, using, INTROSPECTION_RETRY_INTERVAL,
        )
        _failed_at[key] = time.monotonic()
        return ()

    _failed_at.pop(key, None)
    missing = tuple(
        f.name for f in model._meta.local_fields if f.column not in present
    )
    if missing:
        logger.warning(
            '%s on %r is missing %d column(s) the models declare: %s. '
            'Deferring them; see exact#360.',
            table, using, len(missing), ', '.join(missing),
        )

    _cache[key] = missing
    return missing


def defer_missing_columns(queryset):
    """Defer any column of the queryset's model the corpus does not have."""
    missing = missing_columns(queryset.model, queryset.db)
    return queryset.defer(*missing) if missing else queryset


def _deferring_manager_class(cls):
    """A subclass of *cls* whose ``get_queryset`` defers absent columns.

    A subclass rather than a method on the manager instance, for two reasons.
    ``Manager.db_manager(alias)`` returns ``copy.copy(self)`` with ``_db`` set,
    and an instance-level method stays bound to the *original* manager — so the
    copy's alias would be silently ignored. And ``apps.clear_cache()`` discards
    ``Options.managers``, which is a ``cached_property`` holding copies, so a
    per-instance patch disappears with it; binding through the class survives,
    because the copies are made from the patched originals.

    One subclass per manager class, cached: patching the shared
    ``django.db.models.Manager`` itself would reach every app in the project.
    """
    existing = _deferring_classes.get(cls)
    if existing is not None:
        return existing

    class Deferring(cls):
        _defers_missing_columns = True

        def get_queryset(self):
            return defer_missing_columns(super().get_queryset())

        def using(self, alias):
            # `Manager.using` is a passthrough to `get_queryset().using(alias)`,
            # which would decide the deferral against the *router's* alias and
            # then move the query somewhere else — carrying corpus deferrals
            # onto `default` and dropping columns that database really has.
            # Choose the alias first, then decide.
            return defer_missing_columns(
                super(Deferring, self).get_queryset().using(alias)
            )

    Deferring.__name__ = f'{cls.__name__}DeferringMissingColumns'
    Deferring.__qualname__ = Deferring.__name__
    # Importable under the name it reports: `BaseManager.deconstruct` resolves
    # `__module__`/`__name__` and raises if the attribute is not there, and
    # pickling a manager does the same lookup. Keeping the class in this
    # module's namespace makes both work.
    Deferring.__module__ = __name__
    globals()[Deferring.__name__] = Deferring
    _deferring_classes[cls] = Deferring
    return Deferring


def install(app_config):
    """Wrap every manager in `app_config` so its querysets defer what is absent.

    Per-manager rather than per-view: the drift is a property of the corpus, not
    of one endpoint, so whichever endpoint reads the next table to drift would
    need the same treatment. Patching where the querysets are built covers them
    from one place.

    Introspection stays lazy: this runs during app loading, when the database
    may not be reachable yet, so nothing is queried until the first real
    queryset is built.
    """
    if not _enabled():
        return
    if corpus_alias() is None:
        if TRIALS_DB_ALIAS in settings.DATABASES:
            reason = (
                'the %r database addresses the same host, port and name as the '
                'primary one' % TRIALS_DB_ALIAS
            )
        else:
            reason = (
                'no %r database is configured, so every trials model reads from '
                'the primary one' % TRIALS_DB_ALIAS
            )
        logger.warning(
            'TRIALS_DB_TOLERATE_MISSING_COLUMNS is set, but %s. Not arming: this '
            'tolerance exists for a corpus somebody else owns, and applying it to '
            'the primary database would read an un-applied migration as external '
            'drift and answer with less data instead of failing. Point '
            'TRIALS_DATABASE_URL at the corpus, or unset the flag.',
            reason,
        )
        return

    for model in app_config.get_models():
        patched = False
        # `local_managers` is what `Options.managers` copies from, so patching
        # here is what makes the copies — and any later rebuild of them — carry
        # the deferral.
        for manager in model._meta.local_managers:
            if getattr(manager, '_defers_missing_columns', False):
                continue
            manager.__class__ = _deferring_manager_class(type(manager))
            patched = True
        if patched:
            # Copies built before the patch are already cached on _meta.
            model._meta._expire_cache()
