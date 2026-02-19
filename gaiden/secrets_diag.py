from __future__ import annotations

import os
import sys

from gaiden.secrets_loader import bootstrap_openai_env, get_openai_config


def _prefix(text: str) -> str:
    return (text or "").strip()[:10]


def main() -> int:
    try:
        cfg = bootstrap_openai_env(dry_run=True)
    except Exception as exc:
        print("source: (unresolved)")
        print("OPENAI_BASE_URL:", os.getenv("OPENAI_BASE_URL") or "(none)")
        print("key_prefix:", _prefix(os.getenv("OPENAI_API_KEY") or ""), "len:", len((os.getenv("OPENAI_API_KEY") or "").strip()))
        print("healthcheck_ok:", False)
        print("healthcheck_error:", f"{exc.__class__.__name__}: {exc}")
        return 2

    # Run fail-fast gate explicitly for diagnostics.
    try:
        bootstrap_openai_env(dry_run=False)
        ok = True
        err = None
    except Exception as exc:
        ok = False
        err = f"{exc.__class__.__name__}: {exc}"

    resolved = get_openai_config()
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    print("source:", resolved.get("source"))
    print("OPENAI_BASE_URL:", os.getenv("OPENAI_BASE_URL") or "(none)")
    print("key_prefix:", _prefix(key), "len:", len(key))
    print("healthcheck_ok:", ok)
    if err:
        print("healthcheck_error:", err)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

