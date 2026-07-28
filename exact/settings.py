from pathlib import Path
import os
import ssl
import logging
import platform
import dj_database_url
from dotenv import load_dotenv

_logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

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

_dotenv_file = os.environ.get('DOTENV_PATH', '.env')
load_dotenv(os.path.join(BASE_DIR, _dotenv_file))

# Resolved after load_dotenv so a deploy that sets these only in the dotenv
# file is read correctly (not left at the pre-dotenv default). The SECRET_KEY
# guard below depends on ENVIRONMENT reflecting the real deploy target.
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'local')

DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'

# SECRET_KEY must come from the environment in any deployed (non-DEBUG,
# non-local) environment. A missing key there is a hard failure rather than a
# silent fallback to a publicly-known value (which would allow session forgery
# and signed-cookie tampering).
SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG or ENVIRONMENT == 'local':
        SECRET_KEY = 'django-insecure-change-me-in-production'
    else:
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured(
            'SECRET_KEY environment variable must be set when DEBUG is off '
            f'(ENVIRONMENT={ENVIRONMENT!r}).'
        )

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
    'accounts',
    'trials',
    'vocab_mirror',
]

# House OIDC shared-Identity model (issuer, sub). Must be set before the first
# migrate — cannot be swapped on a DB that already migrated django.contrib.auth
# .User, so EXACT only adopts this on a fresh database (see accounts app).
AUTH_USER_MODEL = 'accounts.Identity'

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # Serves the Module Federation remote bundle (remoteEntry.js + chunks)
    # from WHITENOISE_ROOT with cross-origin headers so the ht-phr host can
    # load it. Active only when WHITENOISE_ROOT resolves (Dockerfile.gcp).
    'exact.middleware.CorsWhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# CORS allowlist must be set explicitly per environment. Defaults to empty so
# a misconfigured deploy doesn't accept browser requests from arbitrary
# origins; local dev can opt in with `CORS_ALLOW_ALL_ORIGINS=true`.
CORS_ALLOWED_ORIGINS = [
    x.strip() for x in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',') if x.strip()
]
CORS_ALLOW_ALL_ORIGINS = (
    os.environ.get('CORS_ALLOW_ALL_ORIGINS', 'false').lower() == 'true'
)

# ---------------------------------------------------------------------------
# Security headers (deployed environments)
# Applied when DEBUG is off so local dev over plain HTTP isn't forced onto
# HTTPS-only cookies/HSTS. Mirrors the SoC `soc/settings/_hardening.py` layer.
# TLS terminates at the platform proxy (Cloud Run), so trust the
# X-Forwarded-Proto header to detect the original HTTPS request.
# ---------------------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = ENVIRONMENT in ('prod', 'production')
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'

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

# A single DATABASE_URL (postgis:// or postgres://) is the deploy convention on
# the shared Cloud SQL instance (hk-labs / promop / soc all consume it). The
# engine is forced to the PostGIS backend regardless of scheme, since EXACT's
# models always need GeoDjango. Discrete DATABASE_* vars remain the local-dev
# path.
_database_url = os.environ.get('DATABASE_URL')
if _database_url:
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
    # Parse with dj_database_url (like 'default' above), not a hand-rolled
    # urlparse: the Cloud SQL socket form (postgis://user:pw@/db?host=/cloudsql/
    # INSTANCE) has an empty netloc host, so urlparse().hostname is None and the
    # ?host= socket param is dropped — the trials alias then fell back to
    # localhost:5432 and every trials-app read 500'd with "connection refused".
    DATABASES['trials'] = dj_database_url.parse(
        _trials_db_url,
        engine='django.contrib.gis.db.backends.postgis',
    )

# When the trials DB is a copy we own (not the external read-only catalog),
# enable this so `migrate --database=trials` applies trials migrations to it.
# Default false keeps the external/prod trials DB untouched. See
# exact.db_router.TrialsDatabaseRouter.allow_migrate.
TRIALS_DB_MIGRATE = os.environ.get('TRIALS_DB_MIGRATE', 'false').lower() == 'true'

DATABASE_ROUTERS = ['exact.db_router.TrialsDatabaseRouter']

# Shared Redis cache when REDIS_URL is configured (deploys), else per-process
# LocMemCache (local dev / CI / tests). Keyed off the *raw* env var, not the
# Celery-defaulted `redis_url` below, so an unset REDIS_URL never points the
# cache at a non-existent localhost Redis. A per-process cache here would make
# `ValueOptions.all_options()` (~490 queries) rebuild on every cold gunicorn
# worker — see exact/cache_config.py.
from exact.cache_config import build_caches  # noqa: E402

CACHES = build_caches(
    redis_url=os.environ.get('REDIS_URL'),
    debug=DEBUG,
    environment=ENVIRONMENT,
    force_redis=os.environ.get('CACHE_BACKEND', '').lower() == 'redis',
    redis_ca_certs=os.environ.get('REDIS_SSL_CA_CERTS'),
)

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

# ---------------------------------------------------------------------------
# House API auth standard (OIDC shared-Identity model)
# PartnerAuthentication verifies a Firebase ID token (the only
# browser-appropriate credential); ServiceTokenAuthentication compares a shared
# bearer against SERVICE_AUTH_TOKEN for server-to-server calls. DRF TokenAuth is
# gated behind ENABLE_DRF_TOKEN_AUTH (on for local/dev/tests, off in prod — see
# below); SessionAuthentication stays for the browsable API/admin. Production
# callers use Firebase or the service token. Default-deny stays (IsAuthenticated).
# ---------------------------------------------------------------------------
PARTNER_AUTH_PROVIDERS = [
    'accounts.providers.firebase.FirebaseTokenProvider',
]
FIREBASE_PROJECT_ID = os.environ.get('FIREBASE_PROJECT_ID', 'exact-test' if DEBUG else '')
FIREBASE_SKIP_REVOCATION_CHECK = os.environ.get(
    'FIREBASE_SKIP_REVOCATION_CHECK', 'true' if DEBUG else 'false'
).lower() in ('1', 'true')
AUTH_TOKEN_CACHE_TTL = int(os.environ.get('AUTH_TOKEN_CACHE_TTL', '60'))
SERVICE_AUTH_TOKEN = os.environ.get('SERVICE_AUTH_TOKEN', '')

# Persistent DRF tokens (username/password -> never-expiring bearer, no scope,
# audience, tenant binding, or rotation) are unacceptable for a PHI service in
# production. Keep them only as a transitional credential for the local dev
# harness and tests; deployed environments must use Firebase / the service
# token. The `/api-token-auth/` endpoint is registered only when this is on
# (see exact/urls.py) (#153).
#
# Fail closed: the default enables tokens only for DEBUG or an EXPLICIT
# `ENVIRONMENT=local`. A deployed env that forgets to set ENVIRONMENT (the
# module-level default would otherwise read as 'local') gets tokens OFF, not
# ON — so a misconfigured prod can't silently expose persistent tokens.
_token_auth_default = (
    'true' if (DEBUG or os.environ.get('ENVIRONMENT') == 'local') else 'false'
)
ENABLE_DRF_TOKEN_AUTH = os.environ.get(
    'ENABLE_DRF_TOKEN_AUTH', _token_auth_default
).lower() in ('1', 'true')

# The server-side `?person_id=` resolver fetches a patient from PROMOP using a
# STATIC service token with no binding to the authenticated caller, and PROMOP
# does not enforce row-level authz for that token — so any authenticated caller
# can enumerate person_ids and read other patients' PHI (IDOR, #150/#108).
# No production caller uses this path (the federation host fetches the patient
# via PROMOP `/patient-info/me/` under the end-user's own token and forwards it
# inline), so gate it off by default outside local/DEBUG. Re-enable only once
# caller-identity is forwarded to PROMOP and PROMOP enforces per-user authz.
# Same fail-closed shape as ENABLE_DRF_TOKEN_AUTH: an unset ENVIRONMENT in a
# deploy yields OFF, not ON.
_person_id_lookup_default = (
    'true' if (DEBUG or os.environ.get('ENVIRONMENT') == 'local') else 'false'
)
EXACT_ALLOW_PERSON_ID_LOOKUP = os.environ.get(
    'EXACT_ALLOW_PERSON_ID_LOOKUP', _person_id_lookup_default
).lower() in ('1', 'true')

_AUTH_CLASSES = [
    'accounts.authentication.ServiceTokenAuthentication',
    'accounts.authentication.PartnerAuthentication',
]
if ENABLE_DRF_TOKEN_AUTH:
    _AUTH_CLASSES.append('rest_framework.authentication.TokenAuthentication')
_AUTH_CLASSES.append('rest_framework.authentication.SessionAuthentication')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': _AUTH_CLASSES,
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',
        'user': '300/minute',
        'sync': '10/minute',
    },
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 200,
}

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

# Module Federation remote: WhiteNoise serves the built remote bundle
# (frontend/dist/remote) at the service root so the ht-phr host can fetch
# /remoteEntry.js cross-origin. Set explicitly in the cloud image
# (Dockerfile.gcp); falls back to the on-disk build dir if present.
_whitenoise_root = os.environ.get('WHITENOISE_ROOT', '')
if _whitenoise_root:
    WHITENOISE_ROOT = Path(_whitenoise_root)
else:
    _candidate = BASE_DIR / 'frontend' / 'dist' / 'remote'
    if _candidate.exists():
        WHITENOISE_ROOT = _candidate

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
    # Verify the broker's TLS certificate in any deployed environment. Only
    # local/DEBUG may fall back to CERT_NONE (self-signed dev Redis); using it
    # in prod/staging would permit MITM on Redis traffic that can carry
    # patient-derived task results (#157). REDIS_SSL_CA_CERTS lets a deploy
    # point at a private CA bundle.
    _redis_ssl_local = DEBUG or ENVIRONMENT == 'local'
    _redis_ssl = {
        'ssl_cert_reqs': ssl.CERT_NONE if _redis_ssl_local else ssl.CERT_REQUIRED,
    }
    if _redis_ca := os.environ.get('REDIS_SSL_CA_CERTS'):
        _redis_ssl['ssl_ca_certs'] = _redis_ca
    CELERY_BROKER_USE_SSL = _redis_ssl
    CELERY_REDIS_BACKEND_USE_SSL = dict(_redis_ssl)
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

# ---------------------------------------------------------------------------
# PROMOP patient-info source (#102 — server-side `?person_id=` resolver)
# Empty default is intentional — when unset, the resolver's PROMOP path is
# disabled and the inline `patient_info` payload path stays the only option.
# ---------------------------------------------------------------------------
PROMOP_BASE = os.environ.get('PROMOP_BASE', '')
PROMOP_SERVICE_TOKEN = os.environ.get('PROMOP_SERVICE_TOKEN', '')

# OAuth2 client_credentials for the v1 patient endpoint (#237). When client id +
# secret are set, PromopClient reads /api/v1/patient-records/ with an OAuth bearer
# (scope patient/*.read) minted at {PROMOP_BASE}/o/token/ — replacing the static
# PROMOP_SERVICE_TOKEN + legacy /api/patient-info/ endpoint (sunsets 2026-09-01).
# Register the client in promop with `manage.py create_service_client`.
PROMOP_OAUTH_CLIENT_ID = os.environ.get('PROMOP_OAUTH_CLIENT_ID', '')
PROMOP_OAUTH_CLIENT_SECRET = os.environ.get('PROMOP_OAUTH_CLIENT_SECRET', '')
PROMOP_OAUTH_SCOPE = os.environ.get('PROMOP_OAUTH_SCOPE', 'patient/*.read')
# Defaults to {PROMOP_BASE}/o/token/ when unset.
PROMOP_OAUTH_TOKEN_URL = os.environ.get('PROMOP_OAUTH_TOKEN_URL', '')

# promop concept-graph API (#234; promop ADR 0001 / promop#279) — API + cache
# source of truth for vocabulary/concept-graph data. PROMOP_API_BASE falls back
# to PROMOP_BASE (same promop host); the static token is the shared
# PROMOP_SERVICE_TOKEN declared above. Empty default keeps ConceptGraphClient
# unconfigured (callers get ConceptGraphUnavailable) so nothing silently no-ops.
PROMOP_API_BASE = os.environ.get('PROMOP_API_BASE', '')

# Cache alias CachedConceptGraphClient uses (#234). Points at the shared DB-backed
# 'concept_graph' cache in deployed envs (see exact/cache_config.py); the client falls
# back to 'default' if the alias is absent (e.g. tests). Deployed envs must run
# `python manage.py createcachetable --database=default` to create the cache table.
CONCEPT_GRAPH_CACHE_ALIAS = os.environ.get('CONCEPT_GRAPH_CACHE_ALIAS', 'concept_graph')


# ---------------------------------------------------------------------------
# OMOP therapy matching (epic #4447 cutover).
# When True, trial therapy matching reads the omop_* concept_id columns instead
# of the legacy internal-code columns. Matching is a direct concept_id overlap:
# patient therapies are supplied as concept_ids by the consumer (PROMOP), with no
# EXACT-side translation. OFF by default — capability-gated: only
# flip once the vocab omop_concept_id mappings are loaded and the trial omop_*
# columns are backfilled (see trials/services/omop/ + the shadow-compare report).
# Only the three mapped levels (regimen/component/class) flip; planned/supportive
# stay on the legacy columns until their vocabs gain concept_ids.
# ---------------------------------------------------------------------------
EXACT_OMOP_THERAPY = os.environ.get('EXACT_OMOP_THERAPY', '').lower() in ('1', 'true', 'yes', 'on')


# ---------------------------------------------------------------------------
# Firebase Admin SDK — backs FirebaseTokenProvider (verify_id_token).
# Credential resolution order: an auth emulator (local dev) → the
# FIREBASE_CREDENTIALS_JSON secret injected by infra (Secret Manager) →
# Application Default Credentials (workload identity). A missing credential is
# a soft failure: the provider simply can't verify tokens until configured.
# ---------------------------------------------------------------------------
def _init_firebase_admin():
    try:
        import firebase_admin
        from firebase_admin import credentials as fb_credentials
    except ImportError:
        return

    if firebase_admin._apps:
        return

    options = {"projectId": FIREBASE_PROJECT_ID} if FIREBASE_PROJECT_ID else {}

    if os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
        class _EmulatorCredential(fb_credentials.Base):
            def get_credential(self):
                return None
        cred = _EmulatorCredential()
    else:
        creds_json = os.environ.get("FIREBASE_CREDENTIALS_JSON", "").strip()
        if creds_json:
            import json
            try:
                cred = fb_credentials.Certificate(json.loads(creds_json))
            except Exception:
                _logger.warning("FIREBASE_CREDENTIALS_JSON is not valid; skipping Firebase init")
                return
        else:
            try:
                cred = fb_credentials.ApplicationDefault()
            except Exception:
                return

    try:
        firebase_admin.initialize_app(cred, options)
    except ValueError:
        pass  # Already initialized
    except Exception:
        _logger.warning("Firebase Admin SDK initialization failed", exc_info=True)


_init_firebase_admin()
