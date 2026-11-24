"""Settings for Simple EMR.

Everything that differs between deployments comes from environment variables
(see .env.example). Without POSTGRES_HOST the app falls back to SQLite, which is
only meant for local development.
"""
import ipaddress
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    value = os.environ.get(name)
    return default if value in (None, '') else value


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value in (None, ''):
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [item.strip() for item in env(name, default).split(',') if item.strip()]


DEBUG = env_bool('DJANGO_DEBUG')
SECRET_KEY = env('DJANGO_SECRET_KEY') or ('insecure-dev-key' if DEBUG else None)
if not SECRET_KEY:
    raise ImproperlyConfigured('DJANGO_SECRET_KEY must be set (see .env.example).')
if SECRET_KEY == 'change-me' and not DEBUG:  # public placeholder from .env.example: anyone could forge sessions
    raise ImproperlyConfigured('DJANGO_SECRET_KEY is still "change-me"; set it to a long random string in .env.')

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', '*')
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')

# EMR_HOST lists the addresses the bundled Caddy serves over HTTPS (docker-compose.yml), e.g.
# "serverkliniksehat, 192.168.1.10"; it implies the settings below.
EMR_HOSTS = env('EMR_HOST', '').replace(',', ' ').split()
for _host in EMR_HOSTS:
    if f'https://{_host}' not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f'https://{_host}')

# Set DJANGO_HTTPS=1 when the app sits behind a TLS-terminating reverse proxy.
HTTPS = env_bool('DJANGO_HTTPS') or bool(EMR_HOSTS)
if HTTPS:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
TRUST_X_FORWARDED_FOR = env_bool('TRUST_X_FORWARDED_FOR', HTTPS)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'core.middleware.HttpsRedirectMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.auth.middleware.LoginRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'emr.urls'
WSGI_APPLICATION = 'emr.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.app',
            ],
        },
    },
]

if env('POSTGRES_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env('POSTGRES_DB', 'emr'),
            'USER': env('POSTGRES_USER', 'emr'),
            'PASSWORD': env('POSTGRES_PASSWORD', ''),
            'HOST': env('POSTGRES_HOST'),
            'PORT': env('POSTGRES_PORT', '5432'),
            'CONN_MAX_AGE': 60,
            'CONN_HEALTH_CHECKS': True,
        }
    }
else:
    (BASE_DIR / 'data').mkdir(exist_ok=True)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'data' / 'db.sqlite3',
        }
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'core.User'

# Password rules for staff accounts, adjustable from .env (see .env.example).
# They apply when a password is set in the app; ADMIN_PASSWORD at first start is not checked.
PASSWORD_MIN_LENGTH = int(env('PASSWORD_MIN_LENGTH', '8'))
AUTH_PASSWORD_VALIDATORS = []
if PASSWORD_MIN_LENGTH > 0:
    AUTH_PASSWORD_VALIDATORS.append({
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': PASSWORD_MIN_LENGTH},
    })
if env_bool('PASSWORD_BLOCK_SIMILAR_TO_NAME', True):
    AUTH_PASSWORD_VALIDATORS.append({'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'})
if env_bool('PASSWORD_BLOCK_COMMON', True):
    AUTH_PASSWORD_VALIDATORS.append({'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'})
if env_bool('PASSWORD_BLOCK_ALL_NUMERIC', True):
    AUTH_PASSWORD_VALIDATORS.append({'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'})
_password_classes = {
    'require_digit': env_bool('PASSWORD_REQUIRE_DIGIT'),
    'require_upper': env_bool('PASSWORD_REQUIRE_UPPERCASE'),
    'require_symbol': env_bool('PASSWORD_REQUIRE_SYMBOL'),
}
if any(_password_classes.values()):
    AUTH_PASSWORD_VALIDATORS.append({'NAME': 'core.validators.CharacterClassValidator', 'OPTIONS': _password_classes})

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

# Sliding 12-hour session: a workstation left idle that long has to log in again.
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True

LANGUAGE_CODE = 'id'
TIME_ZONE = env('TIME_ZONE', 'Asia/Jakarta')
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        # collectstatic runs as root at build time; the app runs as a non-root user and must read them.
        'OPTIONS': {'file_permissions_mode': 0o644},
    },
}
if 'test' in sys.argv:
    STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}

# Uploaded files are never served directly; core.views.penunjang checks access first.
MEDIA_ROOT = Path(env('MEDIA_ROOT', str(BASE_DIR / 'media')))
FILE_UPLOAD_PERMISSIONS = 0o640

X_FRAME_OPTIONS = 'SAMEORIGIN'

# App-specific settings
CLINIC_NAME = env('CLINIC_NAME', 'Klinik')
RM_DIGITS = int(env('RM_DIGITS', '6'))
MAX_UPLOAD_MB = int(env('MAX_UPLOAD_MB', '25'))
# Pre-filled on the Laporan page (title of the DM/HT report), e.g. "Contoh".
PUSKESMAS_NAME = env('PUSKESMAS_NAME', '')
# Header of the printed patient card (Kartu periksa): practice name, address line, WhatsApp number.
KARTU_NAMA = env('KARTU_NAMA', CLINIC_NAME)
KARTU_ALAMAT = env('KARTU_ALAMAT', '')
KARTU_WA = env('KARTU_WA', '')


def _phone_host(hosts):
    """Phones often cannot resolve a bare server name, so the QR link prefers an IP address."""
    for host in hosts:
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass
    return hosts[0] if hosts else ''


_qr_host = _phone_host(EMR_HOSTS)
PUBLIC_BASE_URL = env('PUBLIC_BASE_URL', f'https://{_qr_host}' if _qr_host else '')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': env('LOG_LEVEL', 'INFO')},
}
