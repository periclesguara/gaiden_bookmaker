from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
_writer_secret_key = (
    os.environ.get("WRITER_DJANGO_SECRET_KEY", "").strip()
    or os.environ.get("DJANGO_SECRET_KEY", "").strip()
)
if not _writer_secret_key:
    raise RuntimeError("WRITER_DJANGO_SECRET_KEY or DJANGO_SECRET_KEY is required")
SECRET_KEY = _writer_secret_key

DEBUG = os.environ.get("WRITER_DJANGO_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "WRITER_DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "writer",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "writer_portal.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "writer.context_processors.module_links",
    ]},
}]
WSGI_APPLICATION = "writer_portal.wsgi.application"
ASGI_APPLICATION = "writer_portal.asgi.application"

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.environ.get("WRITER_PGDATABASE", os.environ.get("PGDATABASE", "gaiden")),
    "USER": os.environ.get("WRITER_PGUSER", os.environ.get("PGUSER", "gaiden")),
    "PASSWORD": os.environ.get("WRITER_PGPASSWORD", os.environ.get("PGPASSWORD", "")),
    "HOST": os.environ.get("WRITER_PGHOST", os.environ.get("PGHOST", "127.0.0.1")),
    "PORT": os.environ.get("WRITER_PGPORT", os.environ.get("PGPORT", "5432")),
}}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/admin/login/"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

WRITER_HANDOFF_ROOT = os.environ.get(
    "WRITER_HANDOFF_ROOT", str(BASE_DIR.parent / "runtime" / "handoff")
)
GAIDEN_BOOKMAKER_URL = os.environ.get(
    "GAIDEN_BOOKMAKER_URL", "http://127.0.0.1:8000/"
)
