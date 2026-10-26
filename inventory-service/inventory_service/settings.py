import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if os.environ.get("DEBUG", "False") == "True":
        SECRET_KEY = "django-insecure-inventory-dev-key"
    else:
        raise ValueError("DJANGO_SECRET_KEY must be set in production")

DEBUG = os.environ.get("DEBUG", "False") == "True"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost").split(",")]
# Behind the gateway: absolute links (pagination) use the public host and scheme
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "inventory",
    "shared_eventbus",
]

MIDDLEWARE = [
    "shared_metrics.PrometheusMiddleware",
    "shared_auth.audit_middleware.AuditMiddleware",
    "shared_auth.forwarded.ForwardedPrefixMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "inventory_service.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "inventory_service.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "inventory_db"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "inventory_db"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uk-ua"
USE_TZ = True
TIME_ZONE = "Europe/Kyiv"
USE_I18N = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

from shared_auth.settings_base import BASE_REST_FRAMEWORK, BASE_SPECTACULAR_SETTINGS
from shared_auth.cache import redis_caches

# Throttle counters and cached data shared by all workers (Redis database 2)
CACHES = redis_caches(2)

REST_FRAMEWORK = {**BASE_REST_FRAMEWORK}

PRODUCT_SERVICE_URL = os.environ.get("PRODUCT_SERVICE_URL", "http://product-service:8000")
AUDIT_SERVICE_URL = os.environ.get("AUDIT_SERVICE_URL", "http://audit-service:8005")
# Shared with order-service: lets it move stock for guests and webhooks, where no user is signed in
INTERNAL_SERVICE_TOKEN = os.environ.get("INTERNAL_SERVICE_TOKEN", "")

LOW_STOCK_THRESHOLD = int(os.environ.get("LOW_STOCK_THRESHOLD", "5"))

SPECTACULAR_SETTINGS = {
    **BASE_SPECTACULAR_SETTINGS,
    "TITLE": "Inventory Service API",
    "DESCRIPTION": "REST API for warehouse management, stock tracking, and goods receipt",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "inventory": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.environ.get("CSRF_TRUSTED_ORIGINS", "http://localhost").split(",")]
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
