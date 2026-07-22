from django.core.management.base import BaseCommand

from gaiden.application.pipeline.official_body import reconcile_operation
from pipeline.models import OfficialBodyPromotion


class Command(BaseCommand):
    help = "Reconcile interrupted official-body promotions."

    def handle(self, *args, **options):
        pending = OfficialBodyPromotion.objects.exclude(
            state__in=[OfficialBodyPromotion.COMPLETED, OfficialBodyPromotion.FAILED]
        ).order_by("created_at", "id")
        counts = {}
        for operation in pending.iterator():
            result = reconcile_operation(operation)
            counts[result] = counts.get(result, 0) + 1
        summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "nothing_to_do"
        self.stdout.write(self.style.SUCCESS(summary))
