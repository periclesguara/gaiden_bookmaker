#!/usr/bin/env python3
from __future__ import annotations

"""Safely move known repo cleanup residues.

Objetivo: mover residuos conhecidos para backups/repo_cleanup sem apagar.
Entradas: sqlite3, web/data e exports na raiz, se existirem.
Saidas: relatorio em docs/audit/safe_repo_cleanup_YYYYMMDD_HHMMSS.md.
Escreve em disco: sim; com --apply move arquivos e cria pastas. Sem --apply e dry-run.
Altera banco: nao.
Idempotente: sim.
Uso: python scripts/ops/safe_repo_cleanup.py --apply
"""

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[2]
BACKUP_ROOT = ROOT / "backups" / "repo_cleanup"
REPORT_DIR = ROOT / "docs" / "audit"


def move_path(source: Path, dest: Path, apply: bool, actions: list[str]) -> None:
    if not source.exists():
        actions.append(f"skip missing: {source.relative_to(ROOT)}")
        return
    actions.append(f"move: {source.relative_to(ROOT)} -> {dest.relative_to(ROOT)}")
    if apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))


def copy_exports(apply: bool, actions: list[str]) -> None:
    source = ROOT / "exports"
    target = ROOT / "data" / "exports"
    if not source.exists():
        actions.append("skip missing: exports")
        return
    actions.append("copy tree: exports/ -> data/exports/")
    if apply:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="move files instead of dry-run")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    actions: list[str] = []
    for rel in [
        "data/raw",
        "data/preprod",
        "data/normalized",
        "data/md",
        "data/chunks",
        "data/translated",
        "data/frontmatter",
        "data/images",
        "data/covers",
        "data/editions",
        "data/builds",
        "data/exports",
        "data/collections",
        "data/db",
        "data/tmp",
    ]:
        actions.append(f"ensure dir: {rel}")
        if args.apply:
            (ROOT / rel).mkdir(parents=True, exist_ok=True)

    copy_exports(args.apply, actions)
    move_path(ROOT / "exports", BACKUP_ROOT / f"exports.old.{stamp}", args.apply, actions)
    move_path(ROOT / "sqlite3", BACKUP_ROOT / f"sqlite3.suspect.{stamp}", args.apply, actions)
    move_path(ROOT / "web" / "data", BACKUP_ROOT / f"web_data.suspect.{stamp}", args.apply, actions)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / f"safe_repo_cleanup_{stamp}.md"
    mode = "apply" if args.apply else "dry-run"
    report.write_text(
        "\n".join(["# Safe Repo Cleanup", "", f"- Mode: {mode}", "", "## Actions", "", *[f"- {item}" for item in actions]]) + "\n",
        encoding="utf-8",
    )
    print(report.relative_to(ROOT))


if __name__ == "__main__":
    main()
