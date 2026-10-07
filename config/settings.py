import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = os.environ.get("DJANGO_DEBUG", "").lower() in ("1", "true", "yes")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "local-dev-only-not-secret"

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "staff",
    "jobs",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


def postgres_from_url(url):
    """Render hands over one DATABASE_URL; Django wants the parts."""
    u = urlparse(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL must be a postgres:// URL.")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(u.path.lstrip("/")),
        "USER": unquote(u.username or ""),
        "PASSWORD": unquote(u.password or ""),
        "HOST": u.hostname or "",
        "PORT": str(u.port or ""),
        "OPTIONS": dict(parse_qsl(u.query)),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


# Local runs use SQLite; production reads DATABASE_URL, which must point at WoodStar's own
# database, never one shared with another business.
if os.environ.get("DATABASE_URL"):
    DATABASES = {"default": postgres_from_url(os.environ["DATABASE_URL"])}
elif DEBUG:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    raise ImproperlyConfigured("DATABASE_URL must be set when DEBUG is off.")

AUTH_USER_MODEL = "staff.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "app"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # The manifest only exists after collectstatic, which runs in the Render build, not locally.
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "jobs.responses.exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# One process on a small instance, so an in-memory cache is enough for login throttling.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

SESSION_COOKIE_AGE = 7 * 24 * 3600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30

# WhatsApp, through SD Ventures' Gupshup partner account (the same one the clinic side uses).
# WHATSAPP_APP_ID is the Gupshup app of WoodStar's own number. With any of these four missing,
# nothing is sent and the buttons open WhatsApp on the phone instead.
WHATSAPP_API_BASE_URL = os.environ.get("WHATSAPP_API_BASE_URL", "https://partner.gupshup.io")
WHATSAPP_PARTNER_EMAIL = os.environ.get("WHATSAPP_PARTNER_EMAIL", "")
WHATSAPP_PARTNER_SECRET = os.environ.get("WHATSAPP_PARTNER_SECRET", "")
WHATSAPP_APP_ID = os.environ.get("WHATSAPP_APP_ID", "")
# Gupshup replays this as a header on every incoming message; the webhook refuses anything without it.
WHATSAPP_WEBHOOK_SECRET = os.environ.get("WHATSAPP_WEBHOOK_SECRET", "")
WHATSAPP_READY = all([WHATSAPP_PARTNER_EMAIL, WHATSAPP_PARTNER_SECRET, WHATSAPP_APP_ID])

# Where customers reach this dashboard: tracking links in messages, and the address Gupshup
# delivers incoming messages to. Defaults to the first real host in ALLOWED_HOSTS.
_public_host = next((h for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")), "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", f"https://{_public_host}" if _public_host else "http://127.0.0.1:8000").rstrip("/")

# The first job card gets this number; later ones count up from the highest so far.
FIRST_JOB_NUMBER = int(os.environ.get("FIRST_JOB_NUMBER", "1001"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
