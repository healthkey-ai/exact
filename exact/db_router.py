"""
Database router for split-database deployments.

When TRIALS_DATABASE_URL is set, all ``trials`` app models are read
from / written to a separate database (alias ``trials``), while auth,
tokens, sessions, and other Django internals stay on ``default``.

When the ``trials`` alias is not configured the router returns None
everywhere, which makes Django fall back to ``default`` for all models
— i.e. single-database deployments keep working with zero config.
"""

from django.conf import settings

_TRIALS_DB = 'trials'
_TRIALS_APP = 'trials'


def _trials_db_configured():
    return _TRIALS_DB in settings.DATABASES


class TrialsDatabaseRouter:
    """Route ``trials`` app models to the ``trials`` database alias."""

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def db_for_read(self, model, **hints):
        if model._meta.app_label == _TRIALS_APP and _trials_db_configured():
            return _TRIALS_DB
        return None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def db_for_write(self, model, **hints):
        if model._meta.app_label == _TRIALS_APP and _trials_db_configured():
            return _TRIALS_DB
        return None

    # ------------------------------------------------------------------
    # Relations — allow relations only within the same database
    # ------------------------------------------------------------------
    def allow_relation(self, obj1, obj2, **hints):
        app1 = obj1._meta.app_label
        app2 = obj2._meta.app_label
        # Both in trials → OK.  Both NOT in trials → OK.
        # Mixed → deny (cross-db FK).
        if app1 == _TRIALS_APP and app2 == _TRIALS_APP:
            return True
        if app1 != _TRIALS_APP and app2 != _TRIALS_APP:
            return True
        return False

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------
    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if not _trials_db_configured():
            # Single-database mode: everything migrates on default.
            return None

        if app_label == _TRIALS_APP:
            # The trials DB is treated as an externally-managed, read-only
            # catalog by default, so its schema is never migrated. On a copy we
            # actually own and want to keep current (e.g. staging), set
            # TRIALS_DB_MIGRATE=true to apply trials migrations to the 'trials'
            # alias via `migrate --database=trials`.
            if getattr(settings, 'TRIALS_DB_MIGRATE', False):
                return db == _TRIALS_DB
            return False
        else:
            # Everything else only on default.
            return db == 'default'
