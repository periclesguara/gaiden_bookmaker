#!/usr/bin/env python3
"""Compare Django's current model columns with a PostgreSQL schema.

The command is deliberately read-only.  It refuses non-PostgreSQL databases,
starts a read-only transaction, and emits JSON to stdout for Phase 0 audits.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "web"
sys.path[:0] = [str(REPO_ROOT), str(WEB_ROOT)]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gaiden_portal.settings")

import django  # noqa: E402

django.setup()

from django.apps import apps  # noqa: E402
from django.db import connection, transaction  # noqa: E402


def main() -> int:
    if connection.vendor != "postgresql":
        raise SystemExit("Phase 0 schema comparison requires PostgreSQL")

    report: dict[str, object] = {
        "database": connection.settings_dict["NAME"],
        "vendor": connection.vendor,
        "models": [],
        "extra_tables": [],
    }
    model_tables: set[str] = set()

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            actual_tables = set(connection.introspection.table_names(cursor))
            for model in apps.get_models():
                if not model._meta.managed or model._meta.proxy:
                    continue
                table = model._meta.db_table
                model_tables.add(table)
                model_tables.update(
                    field.remote_field.through._meta.db_table
                    for field in model._meta.local_many_to_many
                    if field.remote_field.through._meta.auto_created
                )
                expected = {
                    field.column
                    for field in model._meta.local_concrete_fields
                }
                if table not in actual_tables:
                    actual: set[str] = set()
                else:
                    description = connection.introspection.get_table_description(
                        cursor, table
                    )
                    actual = {
                        getattr(column, "name", column[0])
                        for column in description
                    }
                report["models"].append(
                    {
                        "model": model._meta.label,
                        "table": table,
                        "table_present": table in actual_tables,
                        "missing_columns": sorted(expected - actual),
                        "extra_columns": sorted(actual - expected),
                    }
                )
            report["extra_tables"] = sorted(
                actual_tables
                - model_tables
                - {"django_migrations"}
            )

    report["models"].sort(key=lambda item: item["model"])
    report["summary"] = {
        "missing_tables": sum(
            not item["table_present"] for item in report["models"]
        ),
        "models_with_missing_columns": sum(
            bool(item["missing_columns"]) for item in report["models"]
        ),
        "models_with_extra_columns": sum(
            bool(item["extra_columns"]) for item in report["models"]
        ),
        "extra_table_count": len(report["extra_tables"]),
    }
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
