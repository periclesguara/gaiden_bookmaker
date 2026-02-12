from __future__ import annotations

import os
import sys

from gaiden.secrets_loader import bootstrap_openai_env


def _dump(prefix: str) -> None:
    print(prefix)
    print("PYTHON_EXECUTABLE:", sys.executable)
    print("ENV OPENAI_API_KEY set:", bool(os.getenv("OPENAI_API_KEY")))
    print("ENV OPENAI_API_KEY len:", len(os.getenv("OPENAI_API_KEY", "")))
    print("ENV OPENAI_BASE_URL:", os.getenv("OPENAI_BASE_URL"))
    print("ENV GAIDEN_DEFAULT_MODEL:", os.getenv("GAIDEN_DEFAULT_MODEL"))


def main() -> int:
    _dump("BEFORE load_secrets:")
    try:
        cfg = bootstrap_openai_env(dry_run=True)
    except Exception as exc:
        print("FAIL bootstrap_openai_env:", repr(exc))
        return 2

    _dump("AFTER load_secrets:")
    print("CFG default_model:", cfg.get("default_model"))
    base_url = os.getenv("OPENAI_BASE_URL") or ""
    default_model = cfg.get("default_model")
    ok = True
    if not os.getenv("OPENAI_API_KEY"):
        ok = False
    if not base_url.endswith("/v1"):
        ok = False
    if not default_model:
        ok = False

    if not ok:
        print("STATUS: FAIL")
        print("CONFIG:", cfg)
        return 2

    print("STATUS: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
