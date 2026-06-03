import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'exact.settings')

# Subpath note (see exact/settings.py SUB_PATH block):
# When FORCE_SCRIPT_NAME is set, middlewares need to know the script prefix
# at __init__ time so they can compute correct path prefixes. WhiteNoise in
# particular strips it from STATIC_URL so paths arriving from a (prefix-
# stripping) reverse proxy still match its internal static_prefix.
#
# get_wsgi_application() calls django.setup(set_prefix=False), so without
# this explicit setup() first the script prefix defaults to "/" during
# middleware construction and WhiteNoise misses the prefix-stripped requests.
import django
django.setup()

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
