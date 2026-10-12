from django.contrib import admin

from .models import Collection, CollectionArtifact, CollectionItem, CollectionRunState


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "language", "collection_kind", "status", "item_count")


@admin.register(CollectionItem)
class CollectionItemAdmin(admin.ModelAdmin):
    list_display = ("collection", "order_index", "work_title", "prep_status", "normalize_status", "merge_status")


@admin.register(CollectionArtifact)
class CollectionArtifactAdmin(admin.ModelAdmin):
    list_display = ("collection", "artifact_type", "language", "path", "created_at")


@admin.register(CollectionRunState)
class CollectionRunStateAdmin(admin.ModelAdmin):
    list_display = ("collection", "current_step", "is_locked", "started_at", "finished_at")
