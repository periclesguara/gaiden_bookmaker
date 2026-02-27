from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import django
from django.conf import settings


@dataclass(frozen=True)
class DbSig:
    engine: str
    host: str
    port: str
    name: str
    user: str

    def as_fingerprint(self) -> str:
        if "postgres" in (self.engine or ""):
            return f"postgres://{self.user}@{self.host}:{self.port}/{self.name}"
        return f"{self.engine}://{self.user}@{self.host}:{self.port}/{self.name}"


def _ensure_django_ready() -> None:
    if settings.configured:
        return

    settings_module = (os.getenv("DJANGO_SETTINGS_MODULE") or "").strip()
    if not settings_module:
        raise RuntimeError(
            "DJANGO_SETTINGS_MODULE is not set. Refuse to run DB preflight without explicit Django settings."
        )
    project_root = Path(__file__).resolve().parents[1]
    web_dir = project_root / "web"
    web_dir_str = str(web_dir)
    if web_dir.exists() and web_dir_str not in sys.path:
        sys.path.insert(0, web_dir_str)
    django.setup()


def current_db_sig() -> DbSig:
    _ensure_django_ready()
    db = settings.DATABASES["default"]
    return DbSig(
        engine=str(db.get("ENGINE", "")),
        host=str(db.get("HOST", "")),
        port=str(db.get("PORT", "")),
        name=str(db.get("NAME", "")),
        user=str(db.get("USER", "")),
    )


def require_active_db() -> DbSig:
    expected = (os.getenv("GAIDEN_DB_FINGERPRINT") or "").strip()
    sig = current_db_sig()
    got = sig.as_fingerprint()

    if not expected:
        raise RuntimeError(
            "GAIDEN_DB_FINGERPRINT is not set. Refuse to run without an explicit active DB fingerprint."
        )

    if expected != got:
        raise RuntimeError(
            "ACTIVE DB MISMATCH\n"
            f"Expected: {expected}\n"
            f"Got:      {got}\n"
            "Fix: source scripts/ops/env_gaiden.sh (or set DJANGO/PG* env vars consistently)."
        )

    return sig
