from __future__ import annotations

import json
import re

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from collections_module.models import Collection
from editorial.models import Work
from pipeline.models import BookEditionTemplate, PipelineJob
from web.intake_module.models import IntakeItem


BOOK_CODE_PATTERN = re.compile(r"^book_[0-9]{4,}$")


class Command(BaseCommand):
    help = "Audit duplicate and legacy book codes before enabling automatic allocation."

    def handle(self, *args, **options):
        duplicates = list(
            IntakeItem.objects.exclude(book_code="")
            .values("book_code")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
            .order_by("book_code")
        )
        sources = {
            "intake_items": sorted(
                set(
                    IntakeItem.objects.exclude(book_code="").values_list(
                        "book_code", flat=True
                    )
                )
            ),
            "works": sorted(set(Work.objects.values_list("code", flat=True))),
            "edition_templates": sorted(
                set(
                    BookEditionTemplate.objects.exclude(book_code="").values_list(
                        "book_code", flat=True
                    )
                )
            ),
            "pipeline_jobs": sorted(
                set(
                    PipelineJob.objects.exclude(book_code="").values_list(
                        "book_code", flat=True
                    )
                )
            ),
            "collections": sorted(
                set(
                    Collection.objects.exclude(pipeline_book_code="").values_list(
                        "pipeline_book_code", flat=True
                    )
                )
            ),
        }
        all_codes = sorted({code for codes in sources.values() for code in codes if code})
        source_usage = {
            code: sorted(
                source_name
                for source_name, codes in sources.items()
                if code in codes
            )
            for code in all_codes
        }
        report = {
            "duplicates": duplicates,
            "cross_source_usage": {
                code: source_names
                for code, source_names in source_usage.items()
                if len(source_names) > 1
            },
            "legacy_nonmatching": [
                code for code in all_codes if not BOOK_CODE_PATTERN.fullmatch(code)
            ],
            "sources": sources,
        }
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if duplicates:
            raise CommandError(
                "Duplicate IntakeItem book codes found; repair them before migration."
            )
