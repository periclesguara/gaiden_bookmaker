from django.core.management.base import BaseCommand, CommandError

from editorial.models import Edition
from gaiden.application.builds.final_epub_import import FinalEpubImportError, revalidate_registered_final_build
from gaiden.application.builds.finalized_projects import latest_builds_by_edition, sync_finalized_project


class Command(BaseCommand):
    help = "Reconcile the read projection of editions with valid final builds."

    def add_arguments(self, parser):
        parser.add_argument("--book-code")
        parser.add_argument("--actor", default="management-command")

    def handle(self, *args, **options):
        editions = Edition.objects.select_related("work").order_by("work__code", "id")
        if options.get("book_code"):
            editions = editions.filter(work__code=options["book_code"])
        if not editions.exists():
            raise CommandError("No matching edition exists; no identity was created.")
        projected = 0
        for edition in editions:
            latest = next(
                (item for item in latest_builds_by_edition(book_code=edition.work.code) if item.edition_id == edition.id),
                None,
            )
            if latest and latest.status == latest.STATUS_DONE:
                if latest.validation_report.get("tool") != "EPUBCheck":
                    self.stdout.write(f"{edition.work.code} edition={edition.id}: running mandatory EPUBCheck")
                    try:
                        revalidate_registered_final_build(latest, actor=options["actor"])
                    except FinalEpubImportError as exc:
                        self.stdout.write(
                            self.style.WARNING(
                                f"{edition.work.code} edition={edition.id}: NOT_FINALIZED — {exc}"
                            )
                        )
                        continue
            result = sync_finalized_project(edition.id, actor=options["actor"])
            if result.outcome in {"PROJECTED", "NO_OP"}:
                projected += 1
                self.stdout.write(
                    f"{edition.work.code} edition={edition.id}: {result.outcome} "
                    f"V{result.build.build_version} {result.build.locale}"
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"{edition.work.code} edition={edition.id}: NOT_FINALIZED — " + "; ".join(result.reasons)
                    )
                )
        self.stdout.write(self.style.SUCCESS(f"Finalized projections: {projected}"))
