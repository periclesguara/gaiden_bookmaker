"""Writer workflow services with compatibility exports for manuscript versions."""

from ..legacy_services import (
    WriterIdentityError,
    content_sha256,
    create_version,
    promote_version,
    resolve_editorial_work,
)

__all__ = [
    "WriterIdentityError",
    "content_sha256",
    "create_version",
    "promote_version",
    "resolve_editorial_work",
]
