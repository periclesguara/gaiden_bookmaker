from django.contrib import admin

from .models import BookEditionTemplate, PipelineJob


@admin.register(PipelineJob)
class PipelineJobAdmin(admin.ModelAdmin):
    list_display = ("book_code", "book_title", "language", "stage", "status", "updated_at")
    list_filter = ("language", "stage", "status")
    search_fields = ("book_code", "book_title")


@admin.register(BookEditionTemplate)
class BookEditionTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "book_code",
        "language",
        "title",
        "author_name",
        "publication_year",
        "imprint_name",
        "collection_name",
        "collaborator_name",
        "collaborator_pseudonym",
        "cover_filepath",
        "images_dir",
    )
    list_filter = ("language", "publication_year")
    search_fields = ("book_code", "title", "author_name", "collaborator_name", "collaborator_pseudonym")
