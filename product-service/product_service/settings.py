import os
from pathlib import Path

from shared_auth.cache import redis_caches
from shared_auth.settings_base import BASE_REST_FRAMEWORK, BASE_SPECTACULAR_SETTINGS

# Throttle counters and cached data shared by all workers (Redis database 1)
CACHES = redis_caches(1)

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if os.environ.get("DEBUG", "False") == "True":
        SECRET_KEY = "django-insecure-dev-key-change-in-production"
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
    "products",
    "shared_eventbus",
]

MIDDLEWARE = [
    "shared_metrics.PrometheusMiddleware",
    "shared_auth.audit_middleware.AuditMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "product_service.urls"

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

WSGI_APPLICATION = "product_service.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "products_db"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "products_db"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
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

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUDIT_SERVICE_URL = os.environ.get("AUDIT_SERVICE_URL", "http://audit-service:8005")
ORDER_SERVICE_URL = os.environ.get("ORDER_SERVICE_URL", "http://order-service:8002")
INVENTORY_SERVICE_URL = os.environ.get("INVENTORY_SERVICE_URL", "http://inventory-service:8001")


REST_FRAMEWORK = {
    **BASE_REST_FRAMEWORK,
    "DEFAULT_PAGINATION_CLASS": "products.pagination.StandardResultsSetPagination",
}

SPECTACULAR_SETTINGS = {
    **BASE_SPECTACULAR_SETTINGS,
    "TITLE": "Product Service API",
    "DESCRIPTION": "REST API for managing products and categories",
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
        "products": {
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
