#!/usr/bin/env python3
"""Audit migration files against django_migrations without changing the DB."""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "web"
sys.path[:0] = [str(REPO_ROOT), str(WEB_ROOT)]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gaiden_portal.settings")

import django  # noqa: E402

django.setup()

from django.db import connection, transaction  # noqa: E402


APPS = ("editorial", "intake_module", "pipeline")
OPERATION_RE = re.compile(r"migrations\.([A-Za-z]+)\(")


def migration_metadata(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    dependencies: list[list[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "dependencies"
            for target in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
                dependencies = [list(item) for item in value]
            except (ValueError, TypeError):
                dependencies = [["dynamic", ast.unparse(node.value)]]
            break
    return {
        "path": str(path.relative_to(REPO_ROOT)),
        "dependencies": dependencies,
        "operations": sorted(set(OPERATION_RE.findall(source))),
    }


def main() -> int:
    if connection.vendor != "postgresql":
        raise SystemExit("Phase 0 migration audit requires PostgreSQL")

    files: dict[tuple[str, str], dict[str, object]] = {}
    for app in APPS:
        directory = WEB_ROOT / app / "migrations"
        for path in sorted(directory.glob("[0-9]*.py")):
            files[(app, path.stem)] = migration_metadata(path)

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT app, name, applied FROM django_migrations "
                "WHERE app = ANY(%s) ORDER BY app, name",
                [list(APPS)],
            )
            applied = {
                (app, name): timestamp.isoformat()
                for app, name, timestamp in cursor.fetchall()
            }

    rows = []
    for key in sorted(set(files) | set(applied)):
        app, name = key
        on_disk = files.get(key)
        is_applied = key in applied
        if on_disk and is_applied:
            classification = "PRESENT_AND_MATCHING"
        elif on_disk:
            classification = "FILE_PRESENT_NOT_APPLIED"
        else:
            classification = "APPLIED_FILE_MISSING"
        rows.append(
            {
                "app": app,
                "migration": name,
                "applied_at": applied.get(key),
                "file": on_disk,
                "classification": classification,
            }
        )

    json.dump(
        {
            "database": connection.settings_dict["NAME"],
            "read_only": True,
            "rows": rows,
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
