"""PostgreSQL settings used only by the isolated GitHub test job."""

from .settings import *  # noqa: F403,F401


PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Tests must opt in again at the importer call site. Production settings never
# expose this switch and therefore always execute EPUBCheck.
GAIDEN_ALLOW_EPUBCHECK_SKIP_FOR_TESTS = True
