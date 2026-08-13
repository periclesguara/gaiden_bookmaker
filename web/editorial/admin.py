from django.contrib import admin

from editorial.models import EditionMetadata


@admin.register(EditionMetadata)
class EditionMetadataAdmin(admin.ModelAdmin):
    list_display = (
        "edition_code",
        "book_code",
        "regional_language",
        "status",
        "updated_at",
    )
    list_filter = ("status", "regional_language", "work_type", "edition_format")
    search_fields = (
        "edition_code",
        "slug",
        "commercial_title",
        "edition__work__code",
    )
