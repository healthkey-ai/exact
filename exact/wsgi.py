import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'exact.settings')
_application = get_wsgi_application()

# Also gate direct Gunicorn/runserver callers that bypass the Docker entrypoint.
# The inspector closes its dedicated connection before worker forks/preload.
from trials.schema_contract import require_trials_schema
require_trials_schema()
application = _application
