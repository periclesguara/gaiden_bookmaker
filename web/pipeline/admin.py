from django.contrib import admin

from editorial.models import (
    Contributor,
    Edition,
    EditionPipeline,
    EditionText,
    Language,
    Seal,
    Work,
)


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "native_name", "is_active")
    search_fields = ("code", "name", "native_name")


@admin.register(Seal)
class SealAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "is_active")
    search_fields = ("slug", "name")


@admin.register(Contributor)
class ContributorAdmin(admin.ModelAdmin):
    list_display = ("name", "role")
    search_fields = ("name",)


@admin.register(Work)
class WorkAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "author", "year")
    search_fields = ("code", "title", "author__name")


@admin.register(Edition)
class EditionAdmin(admin.ModelAdmin):
    list_display = ("id", "work", "language", "seal", "edition_year", "raw_source_path")
    search_fields = ("work__code", "work__title")
    list_filter = ("language", "seal")


@admin.register(EditionPipeline)
class EditionPipelineAdmin(admin.ModelAdmin):
    list_display = ("edition", "current_stage", "translation_language", "raw_at", "normalized_at", "chunked_at")
    list_filter = ("current_stage",)


@admin.register(EditionText)
class EditionTextAdmin(admin.ModelAdmin):
    list_display = ("edition", "raw_path", "normalized_path", "updated_at")
