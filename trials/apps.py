from django.apps import AppConfig


class TrialsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trials'

    def ready(self):
        import trials.signals  # noqa: F401 — register signal handlers

        # No-op unless TRIALS_DB_TOLERATE_MISSING_COLUMNS is set. Introspection
        # is lazy, so nothing touches the database during app loading.
        from trials.db_compat import install
        install(self)
