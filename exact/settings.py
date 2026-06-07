from pathlib import Path
import os
import ssl
import logging
import platform
from dotenv import load_dotenv

_logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'local')

# GDAL / GEOS — required by django.contrib.gis (PostGIS backend).
# On macOS with Homebrew the libraries are not on the default search path,
# so we resolve them here if they haven't been set in the environment already.
if platform.system() == 'Darwin':
    import subprocess

    def _brew_prefix(pkg):
        try:
            return subprocess.check_output(
                ['brew', '--prefix', pkg], stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            return ''

    if not os.environ.get('GDAL_LIBRARY_PATH'):
        _gdal_prefix = _brew_prefix('gdal')
        if _gdal_prefix:
            GDAL_LIBRARY_PATH = os.path.join(_gdal_prefix, 'lib', 'libgdal.dylib')

    if not os.environ.get('GEOS_LIBRARY_PATH'):
        _geos_prefix = _brew_prefix('geos')
        if _geos_prefix:
            GEOS_LIBRARY_PATH = os.path.join(_geos_prefix, 'lib', 'libgeos_c.dylib')

load_dotenv(os.path.join(BASE_DIR, '.env'))

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-me-in-production')

DEBUG = os.environ.get('DEBUG', 'true').lower() == 'true'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

CSRF_TRUSTED_ORIGINS = [x for x in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if x]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.gis',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'drf_yasg',
    'trials',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = 'exact.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'exact.wsgi.application'

# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------

_database_url = os.environ.get('DATABASE_URL')
if _database_url:
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.parse(
            _database_url,
            engine='django.contrib.gis.db.backends.postgis',
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.contrib.gis.db.backends.postgis',
            'NAME': os.environ.get('DATABASE_NAME', 'exact'),
            'USER': os.environ.get('DATABASE_USER', 'exact'),
            'PASSWORD': os.environ.get('DATABASE_PASSWORD', ''),
            'HOST': os.environ.get('DATABASE_HOST', 'localhost'),
            'PORT': os.environ.get('DATABASE_PORT', '5432'),
        }
    }

# Optional separate database for trials data.
# Set TRIALS_DATABASE_URL to enable (e.g. postgresql://user:pass@host:5432/dbname).
_trials_db_url = os.environ.get('TRIALS_DATABASE_URL')
if _trials_db_url:
    from urllib.parse import urlparse
    _parsed = urlparse(_trials_db_url)
    DATABASES['trials'] = {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': _parsed.path.lstrip('/'),
        'USER': _parsed.username or 'exact',
        'PASSWORD': _parsed.password or '',
        'HOST': _parsed.hostname or 'localhost',
        'PORT': str(_parsed.port or 5432),
    }

DATABASE_ROUTERS = ['exact.db_router.TrialsDatabaseRouter']

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 200,
    'DEFAULT_SCHEMA_CLASS': 'rest_framework.schemas.coreapi.AutoSchema',
}

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# ---------------------------------------------------------------------------
# Celery (only needed if you run async tasks, e.g. geolocation lookup)
# ---------------------------------------------------------------------------
redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
CELERY_BROKER_URL = redis_url
CELERY_RESULT_BACKEND = redis_url
if CELERY_BROKER_URL.startswith('rediss://'):
    CELERY_BROKER_USE_SSL = {'ssl_cert_reqs': ssl.CERT_NONE}
    CELERY_REDIS_BACKEND_USE_SSL = {'ssl_cert_reqs': ssl.CERT_NONE}
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# ---------------------------------------------------------------------------
# PostGIS / GeoPos
# ---------------------------------------------------------------------------
# On Linux the system GDAL/GEOS libs are found automatically; only override
# when explicitly provided (e.g. macOS Homebrew installs).
if _gdal := os.environ.get('GDAL_LIBRARY_PATH'):
    GDAL_LIBRARY_PATH = _gdal
if _geos := os.environ.get('GEOS_LIBRARY_PATH'):
    GEOS_LIBRARY_PATH = _geos

# ---------------------------------------------------------------------------
# Search traces (debug)
# ---------------------------------------------------------------------------
ADD_SEARCH_TRIALS_TRACES = os.environ.get('ADD_SEARCH_TRIALS_TRACES', 'false') == 'true'
