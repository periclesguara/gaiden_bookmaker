from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from editorial.models import Edition


@receiver(post_save, sender=Edition)
def ensure_pipeline(sender, instance: Edition, created: bool, **kwargs) -> None:
    from pipeline.services.pipeline_stage_sync import sync_pipeline_stage

    sync_pipeline_stage(instance, created=created)
