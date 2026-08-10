"""Tolerate a trials database whose schema predates the current models.

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

* Only columns are handled. A missing *table* means a relation the corpus has
  never held, and quietly serving empty results for it would hide a real gap.
* Deferred columns still raise if something reads them. Django loads a deferred
  attribute on access, which re-queries the missing column. That is the right
  outcome: a filter over data the corpus does not carry should fail loudly
  rather than silently return matches computed from nothing.
* Off unless ``TRIALS_DB_TOLERATE_MISSING_COLUMNS`` is set, so a deployment
  whose database does match the models behaves exactly as before.
"""
import logging

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)

_cache = {}


def missing_columns(model, using):
    """Model field names whose columns are absent from the ``using`` database.

    Empty when the feature is off, when the table itself is missing, or when
    introspection fails — every one of those is a case where deferring would
    guess rather than know. Cached per (model, alias): the answer only changes
    when the database schema does, which does not happen inside a process.
    """
    if not getattr(settings, 'TRIALS_DB_TOLERATE_MISSING_COLUMNS', False):
        return ()

    key = (model._meta.label, using)
    if key in _cache:
        return _cache[key]

    table = model._meta.db_table
    try:
        with connections[using].cursor() as cursor:
            cursor.execute(
                'SELECT column_name FROM information_schema.columns '
                'WHERE table_schema = current_schema() AND table_name = %s',
                [table],
            )
            present = {row[0] for row in cursor.fetchall()}
    except Exception:
        logger.exception('Could not introspect %s on %r; deferring nothing', table, using)
        return ()

    if not present:
        # No such table. Not something to paper over — let the query say so.
        missing = ()
    else:
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
    """Defer any column of the queryset's model the database does not have."""
    missing = missing_columns(queryset.model, queryset.db)
    return queryset.defer(*missing) if missing else queryset
