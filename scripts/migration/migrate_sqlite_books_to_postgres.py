#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import sys
from types import SimpleNamespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "web"
for candidate in (str(REPO_ROOT), str(WEB_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def main() -> int:
    sqlite_path = Path(os.environ.get("GAIDEN_SQLITE_PATH", "web/db.sqlite3"))
    book_codes = os.environ.get("GAIDEN_MIGRATE_BOOKS", "book_0009,book10")
    wanted = [code.strip() for code in book_codes.split(",") if code.strip()]
    if not wanted:
        raise SystemExit("No book codes requested.")

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gaiden_portal.settings")

    import django

    django.setup()

    from django.db import transaction
    from editorial.models import Contributor, Edition, EditionPipeline, EditionText, Language, Seal, Work
    from pipeline.models import BookEditionTemplate
    from pipeline.views import _insert_edition_row_legacy_schema

    if not sqlite_path.exists():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")

    con = sqlite3.connect(sqlite_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    placeholders = ",".join("?" for _ in wanted)

    work_rows = cur.execute(
        f"SELECT * FROM work WHERE code IN ({placeholders}) ORDER BY code",
        wanted,
    ).fetchall()
    if not work_rows:
        raise SystemExit(f"No matching works found in SQLite for: {wanted}")

    contributor_map = {
        row["id"]: dict(row)
        for row in cur.execute(
            "SELECT * FROM contributor WHERE id IN (SELECT author_id FROM work WHERE code IN (%s))"
            % placeholders,
            wanted,
        ).fetchall()
    }
    language_ids = {
        row["original_language_id"] for row in work_rows
    }
    edition_rows = cur.execute(
        "SELECT * FROM edition WHERE work_id IN (%s) ORDER BY id" % ",".join(str(row["id"]) for row in work_rows)
    ).fetchall()
    language_ids.update(row["language_id"] for row in edition_rows)
    language_map = {
        row["id"]: dict(row)
        for row in cur.execute(
            "SELECT * FROM language WHERE id IN (%s)" % ",".join(str(i) for i in sorted(language_ids))
        ).fetchall()
    }
    seal_ids = {row["seal_id"] for row in edition_rows}
    seal_map = {
        row["id"]: dict(row)
        for row in cur.execute(
            "SELECT * FROM seal WHERE id IN (%s)" % ",".join(str(i) for i in sorted(seal_ids))
        ).fetchall()
    }
    pipeline_rows = {
        row["edition_id"]: dict(row)
        for row in cur.execute(
            "SELECT * FROM edition_pipeline WHERE edition_id IN (%s)"
            % ",".join(str(row["id"]) for row in edition_rows)
        ).fetchall()
    }
    text_rows = {
        row["edition_id"]: dict(row)
        for row in cur.execute(
            "SELECT * FROM edition_text WHERE edition_id IN (%s)"
            % ",".join(str(row["id"]) for row in edition_rows)
        ).fetchall()
    }
    template_rows = {
        row["book_code"]: dict(row)
        for row in cur.execute(
            f"SELECT * FROM pipeline_bookeditiontemplate WHERE book_code IN ({placeholders})",
            wanted,
        ).fetchall()
    }

    migrated: list[tuple[str, int]] = []

    def _upsert_language(row: dict) -> Language:
        obj, _ = Language.objects.get_or_create(
            code=row["code"],
            defaults={
                "name": row["name"],
                "native_name": row["native_name"],
                "is_active": bool(row["is_active"]),
            },
        )
        return obj

    def _upsert_contributor(row: dict) -> Contributor:
        obj, _ = Contributor.objects.get_or_create(
            name=row["name"],
            role=row["role"] or "AUTHOR",
        )
        return obj

    def _upsert_seal(row: dict) -> Seal:
        existing = Seal.objects.filter(name__iexact=row["name"]).order_by("id").first()
        if existing:
            return existing
        obj, _ = Seal.objects.get_or_create(
            slug=row["slug"],
            defaults={
                "name": row["name"],
                "description": row["description"] or "",
                "is_active": bool(row["is_active"]),
            },
        )
        return obj

    def _ensure_work(
        *,
        code: str,
        title: str,
        author: Contributor,
        original_language: Language,
        publisher: str,
        year: int | None,
        is_public_domain: bool,
        source_format: str,
    ) -> Work:
        existing = Work.objects.filter(code=code).first()
        if existing:
            Work.objects.filter(pk=existing.pk).update(
                title=title,
                original_language=original_language,
                author=author,
                publisher=publisher,
                year=year,
                is_public_domain=is_public_domain,
            )
            return Work.objects.get(pk=existing.pk)

        from django.db import connection

        candidate_values: dict[str, object] = {
            "code": code,
            "title": title,
            "publisher": publisher,
            "year": year,
            "is_public_domain": is_public_domain,
            "author_id": author.id,
            "original_language_id": original_language.id,
            "subtitle": "",
            "enabled_languages": f'[\"{original_language.code}\"]',
            "source_format": source_format,
            "notes": "",
        }
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, is_nullable, column_default, data_type, udt_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'work'
                ORDER BY ordinal_position
                """
            )
            rows = cursor.fetchall()
            table_columns = {row[0] for row in rows}
            insert_columns = [name for name in candidate_values if name in table_columns]
            placeholders = ", ".join(["%s"] * len(insert_columns))
            columns_sql = ", ".join(insert_columns)
            values = [candidate_values[name] for name in insert_columns]
            cursor.execute(
                f"INSERT INTO work ({columns_sql}) VALUES ({placeholders}) ON CONFLICT (code) DO NOTHING",
                values,
            )
        return Work.objects.get(code=code)

    with transaction.atomic():
        for work_row in work_rows:
            author = _upsert_contributor(contributor_map[work_row["author_id"]])
            original_language = _upsert_language(language_map[work_row["original_language_id"]])
            template_row = template_rows.get(work_row["code"]) or {}
            work_obj = _ensure_work(
                code=work_row["code"],
                title=work_row["title"],
                author=author,
                original_language=original_language,
                publisher=work_row["publisher"] or "",
                year=work_row["year"],
                is_public_domain=bool(work_row["is_public_domain"]),
                source_format=(template_row.get("text_source_mode") or "txt"),
            )

            edition_row = next(row for row in edition_rows if row["work_id"] == work_row["id"])
            edition_language = _upsert_language(language_map[edition_row["language_id"]])
            seal = _upsert_seal(seal_map[edition_row["seal_id"]])
            edition_obj = Edition.objects.filter(
                work=work_obj,
                language=edition_language,
                seal=seal,
            ).first()

            if edition_obj is None:
                helper_template = SimpleNamespace(
                    book_code=work_row["code"],
                    language=edition_language.code,
                    title=edition_row["title"] or work_row["title"],
                    subtitle=edition_row["subtitle"] or "",
                    author_name=edition_row["author"] or author.name,
                    publication_year=edition_row["publication_year"] or 2026,
                    imprint_name=edition_row["imprint_name"] or edition_row["publisher"] or "",
                    collection_name="",
                    collaborator_name="",
                    collaborator_pseudonym="",
                    collaborator_roles="",
                    seal_name=edition_row["seal_name"] or seal.name,
                    editor_name=edition_row["editor"] or "",
                    translator_name=edition_row["translator"] or "",
                    adapter_name=edition_row["adapter"] or "",
                    city_name=edition_row["city"] or "",
                    country_name=edition_row["country"] or "",
                    editorial_name=(template_row.get("editorial_name") or ""),
                    edition_year=edition_row["edition_year"],
                    edition_copyright_holder=(template_row.get("edition_copyright_holder") or ""),
                    cover_filepath=edition_row["cover_filepath"] or "",
                    images_dir=(template_row.get("images_dir") or ""),
                    frontispiece_text=edition_row["frontispiece_template"] or "",
                    copyright_text=edition_row["copyright_template"] or "",
                    about_edition_text=edition_row["about_edition_template"] or "",
                    about_contributor_text=edition_row["about_contributor_template"] or "",
                    text_source_mode=(template_row.get("text_source_mode") or "auto"),
                )
                _insert_edition_row_legacy_schema(
                    work_id=work_obj.id,
                    language_id=edition_language.id,
                    seal_id=seal.id,
                    language_code=edition_language.code,
                    seal_name=helper_template.seal_name or seal.name,
                    template=helper_template,
                    author_name=helper_template.author_name,
                )
                edition_obj = Edition.objects.filter(
                    work=work_obj,
                    language=edition_language,
                    seal=seal,
                ).order_by("-id").first()
                if edition_obj is None:
                    raise RuntimeError(f"Edition migration failed for {work_row['code']}")

            Edition.objects.filter(pk=edition_obj.pk).update(
                publisher=edition_row["publisher"] or "",
                edition_year=edition_row["edition_year"],
                raw_source_path=edition_row["raw_source_path"] or "",
                title=edition_row["title"] or "",
                subtitle=edition_row["subtitle"] or "",
                author=edition_row["author"] or "",
                adapter=edition_row["adapter"] or "",
                translator=edition_row["translator"] or "",
                editor=edition_row["editor"] or "",
                about_edition_text=edition_row["about_edition_template"] or "",
                publication_year=edition_row["publication_year"] or 2026,
                city=edition_row["city"] or "Rio de Janeiro",
                country=edition_row["country"] or "Brasil",
                imprint_name=edition_row["imprint_name"] or "RinoBooks",
                seal_name=edition_row["seal_name"] or seal.name,
                frontispiece_template=edition_row["frontispiece_template"] or "",
                copyright_template=edition_row["copyright_template"] or "",
                about_edition_template=edition_row["about_edition_template"] or "",
                about_contributor_template=edition_row["about_contributor_template"] or "",
                cover_filepath=edition_row["cover_filepath"] or "",
                language_code=edition_row["language_code"] or edition_language.code,
                lock_translate=bool(edition_row["lock_translate"]),
                lock_refine=bool(edition_row["lock_refine"]),
                lock_polish=bool(edition_row["lock_polish"]),
                miolo_source_stage=edition_row["miolo_source_stage"] or "",
            )

            pipeline_row = pipeline_rows.get(edition_row["id"])
            if pipeline_row:
                EditionPipeline.objects.update_or_create(
                    edition=edition_obj,
                    defaults={
                        "core_last_txt_path": pipeline_row["core_last_txt_path"],
                        "md_language": pipeline_row["md_language"],
                        "frontmatter_language": pipeline_row["frontmatter_language"],
                        "frontmatter_locked": bool(pipeline_row["frontmatter_locked"]),
                        "current_stage": pipeline_row["current_stage"],
                        "translation_language": pipeline_row["translation_language"] or "",
                        "raw_at": pipeline_row["raw_at"],
                        "normalized_at": pipeline_row["normalized_at"],
                        "split_at": pipeline_row["split_at"],
                        "chunked_at": pipeline_row["chunked_at"],
                        "translated_at": pipeline_row["translated_at"],
                        "refined_at": pipeline_row["refined_at"],
                        "merged_at": pipeline_row["merged_at"],
                        "polished_at": pipeline_row["polished_at"],
                        "miolo_md_at": pipeline_row["miolo_md_at"],
                        "final_md_at": pipeline_row["final_md_at"],
                        "last_log": pipeline_row["last_log"] or "",
                    },
                )

            text_row = text_rows.get(edition_row["id"])
            if text_row:
                EditionText.objects.update_or_create(
                    edition=edition_obj,
                    defaults={
                        "raw_text": text_row["raw_text"] or "",
                        "normalized_text": text_row["normalized_text"] or "",
                        "raw_path": text_row["raw_path"] or "",
                        "normalized_path": text_row["normalized_path"] or "",
                    },
                )

            if template_row:
                BookEditionTemplate.objects.update_or_create(
                    book_code=template_row["book_code"],
                    language=template_row["language"],
                    defaults={
                        "title": template_row["title"] or "",
                        "subtitle": template_row["subtitle"] or "",
                        "author_name": template_row["author_name"] or "",
                        "publication_year": template_row["publication_year"] or 2026,
                        "imprint_name": template_row["imprint_name"] or "",
                        "collection_name": template_row["collection_name"] or "",
                        "collaborator_name": template_row["collaborator_name"] or "",
                        "collaborator_pseudonym": template_row["collaborator_pseudonym"] or "",
                        "collaborator_roles": template_row["collaborator_roles"] or "",
                        "seal_name": template_row["seal_name"] or "",
                        "editor_name": template_row["editor_name"] or "",
                        "translator_name": template_row["translator_name"] or "",
                        "adapter_name": template_row["adapter_name"] or "",
                        "city_name": template_row["city_name"] or "",
                        "country_name": template_row["country_name"] or "",
                        "editorial_name": template_row["editorial_name"] or "",
                        "edition_year": template_row["edition_year"],
                        "edition_copyright_holder": template_row["edition_copyright_holder"] or "",
                        "cover_filepath": template_row["cover_filepath"] or "",
                        "images_dir": template_row["images_dir"] or "",
                        "frontispiece_text": template_row["frontispiece_text"] or "",
                        "copyright_text": template_row["copyright_text"] or "",
                        "about_edition_text": template_row["about_edition_text"] or "",
                        "about_contributor_text": template_row["about_contributor_text"] or "",
                        "text_source_mode": template_row["text_source_mode"] or "auto",
                    },
                )

            migrated.append((work_obj.code, edition_obj.id))

    for code, edition_id in migrated:
        print(f"MIGRATED {code} -> edition_id={edition_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
