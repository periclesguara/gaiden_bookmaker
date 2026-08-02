"""Isolated SQLite settings for unit/integration tests.

Production continues to use PostgreSQL through ``gaiden_portal.settings``.
The author_studio migration that creates pgvector is intentionally replaced by
model synchronization here because SQLite has no PostgreSQL extensions.
"""

from .settings import *  # noqa: F403,F401


DATABASES = {  # noqa: F405
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

MIGRATION_MODULES = {
    "author_studio": None,
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
