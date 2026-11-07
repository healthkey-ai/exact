"""Firebase ID-token verification settings (#411).

Factored out of settings.py so the defaults are unit-testable without
reloading the frozen settings module (same shape as ``exact.cache_config``).

Both values are authentication controls, so neither is derived from ``DEBUG``.
They used to be: ``DEBUG=true`` silently turned revocation checking off and
pointed verification at a hard-coded ``exact-test`` project, so turning on
``DEBUG`` to read a traceback also changed who could authenticate.
"""

_TRUE = ('1', 'true')


def firebase_auth_settings(environ):
    """Return ``(project_id, skip_revocation_check)`` from ``environ``.

    ``FIREBASE_PROJECT_ID`` defaults to empty: the Admin SDK then takes the
    project from its credential or ``GOOGLE_CLOUD_PROJECT``. The Auth emulator
    does not supply one, so local development sets it explicitly.

    ``FIREBASE_SKIP_REVOCATION_CHECK`` defaults to off, so tokens are checked
    for revocation and disabled users. A deployment that cannot make that call
    (its service account lacks ``firebaseauth.admin``, see #410) opts out by
    setting it to ``true`` explicitly.
    """
    project_id = environ.get('FIREBASE_PROJECT_ID', '')
    skip_revocation_check = (
        environ.get('FIREBASE_SKIP_REVOCATION_CHECK', 'false').strip().lower() in _TRUE
    )
    return project_id, skip_revocation_check
