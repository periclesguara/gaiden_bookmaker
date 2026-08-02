from django.contrib import admin

from .models import Author, CanonicalText, Work, WorkChunk, WorkSource, WorkSplit


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "created_at", "updated_at")
    search_fields = ("name", "canonical_name", "code")
    readonly_fields = ("canonical_name", "slug", "code", "created_at", "updated_at")


@admin.register(Work)
class WorkAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "author", "status", "updated_at")
    search_fields = ("title", "canonical_title", "code", "author__name", "author__code")
    list_filter = ("status", "author")
    readonly_fields = ("canonical_title", "slug", "code", "created_at", "updated_at")


@admin.register(WorkSource)
class WorkSourceAdmin(admin.ModelAdmin):
    list_display = ("code", "work", "extension", "size_bytes", "extraction_status", "created_at")
    search_fields = ("code", "work__title", "work__code", "sha256", "original_filename")
    list_filter = ("extraction_status", "source_provider", "extension", "work__author")
    readonly_fields = ("code", "stored_file", "sha256", "size_bytes", "created_at")


@admin.register(CanonicalText)
class CanonicalTextAdmin(admin.ModelAdmin):
    list_display = ("code", "work", "status", "word_count", "character_count", "updated_at")
    search_fields = ("code", "work__title", "work__code", "sha256")
    list_filter = ("status", "work__author")
    readonly_fields = ("code", "sha256", "character_count", "word_count", "created_at", "updated_at")


@admin.register(WorkSplit)
class WorkSplitAdmin(admin.ModelAdmin):
    list_display = ("work", "status", "chunk_count", "started_at", "completed_at")
    search_fields = ("work__code", "work__title", "source_sha256")
    list_filter = ("status", "work__author")
    readonly_fields = ("source_sha256", "chunk_count", "started_at", "completed_at", "updated_at")


@admin.register(WorkChunk)
class WorkChunkAdmin(admin.ModelAdmin):
    list_display = ("code", "work", "sequence", "unit_type", "estimated_tokens", "word_count")
    search_fields = ("code", "work__code", "work__title", "unit_title", "sha256")
    list_filter = ("unit_type", "work__author")
    readonly_fields = ("code", "sha256", "character_count", "word_count", "estimated_tokens", "created_at")
