from __future__ import annotations

import sys
from pathlib import Path


def assert_venv(repo_root: str | Path) -> None:
    repo_root = Path(repo_root).resolve()
    venv_python = repo_root / ".venv" / "bin" / "python"
    exe = Path(sys.executable).resolve()

    # if venv exists, require using it
    if venv_python.exists() and exe != venv_python.resolve():
        raise SystemExit(
            f"[GAIDEN] Wrong python executable.\n"
            f"  expected: {venv_python}\n"
            f"  actual:   {exe}\n"
            f"Fix:\n"
            f"  source {repo_root}/.venv/bin/activate\n"
        )
