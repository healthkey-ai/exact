import os

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Warning, register


class UserStateConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'user_state'
    verbose_name = 'Trials-page user state'

    def ready(self):
        register(check_erasure_is_reachable)


def check_erasure_is_reachable(app_configs, **kwargs):
    """Say so when the only remote erasure path cannot authenticate.

    `SERVICE_AUTH_TOKEN` defaults to empty, and with it empty
    `ServiceTokenAuthentication` denies everyone — so PROMOP's "this patient
    is deleted" call gets a 401 indistinguishable from a wrong token, and a
    fire-and-forget caller will never notice.

    A warning rather than an error, because `manage.py forget_identity` is
    still a path. And NOT raised in local or DEBUG: nothing is calling this
    endpoint on a laptop, `.env.example` ships the secret empty, and a
    warning that fires on every developer machine and every CI run is one
    nobody reads by the time it means something.
    """
    if getattr(settings, 'SERVICE_AUTH_TOKEN', '').strip():
        return []
    if settings.DEBUG or os.environ.get('ENVIRONMENT') == 'local':
        return []
    return [
        Warning(
            'user_state is installed but SERVICE_AUTH_TOKEN is empty, so '
            '/user-state/internal/forget/ answers 401 to every caller.',
            hint=(
                'Set SERVICE_AUTH_TOKEN so PROMOP can tell EXACT a patient '
                'was deleted, or erase by hand with '
                '`manage.py forget_identity --issuer … --sub …`.'
            ),
            id='user_state.W001',
        )
    ]
