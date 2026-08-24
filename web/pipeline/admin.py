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
from pipeline.models import (
    IncrementalBlock,
    IncrementalEdition,
    IncrementalImportEvent,
    IncrementalImportRun,
    IntakeAuditEvent,
    IntakeBatch,
    IntakeCounter,
    IntakeItem,
    ManualTranslationJob,
    ProductionBookmark,
    TranslationJobEvent,
    TranslationUnit,
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
    list_display = ("edition", "current_stage", "translation_language", "raw_at", "normalized_at", "split_at")
    list_filter = ("current_stage",)


@admin.register(EditionText)
class EditionTextAdmin(admin.ModelAdmin):
    list_display = ("edition", "raw_path", "normalized_path", "updated_at")


@admin.register(IncrementalEdition)
class IncrementalEditionAdmin(admin.ModelAdmin):
    list_display = (
        "edition_id",
        "locale",
        "last_contiguous_sequence",
        "next_sequence",
        "status",
        "updated_at",
    )
    search_fields = ("edition_id", "work_id", "book_code")
    list_filter = ("locale", "status")


@admin.register(IncrementalBlock)
class IncrementalBlockAdmin(admin.ModelAdmin):
    list_display = ("block_id", "sequence", "version", "status", "is_current", "updated_at")
    search_fields = ("edition__edition_id", "block_id", "file_name", "content_sha256")
    list_filter = ("status", "is_current", "edition__locale")


@admin.register(IncrementalImportRun)
class IncrementalImportRunAdmin(admin.ModelAdmin):
    list_display = ("run_id", "job_id", "import_attempt", "status", "started_at", "completed_at")
    search_fields = ("run_id", "job_id", "edition__edition_id", "manifest_sha256")
    list_filter = ("status",)


@admin.register(IncrementalImportEvent)
class IncrementalImportEventAdmin(admin.ModelAdmin):
    list_display = ("run", "sequence", "block_id", "action", "created_at")
    search_fields = ("run__run_id", "block_id")
    list_filter = ("action",)


@admin.register(IntakeBatch)
class IntakeBatchAdmin(admin.ModelAdmin):
    list_display = ("batch_code", "name", "source", "drive_source_path", "status", "updated_at")
    search_fields = ("batch_code", "name", "drive_source_path")
    list_filter = ("source", "status")


@admin.register(IntakeItem)
class IntakeItemAdmin(admin.ModelAdmin):
    list_display = ("book_code", "batch", "relative_path", "preview_operation", "status", "attempt_count")
    search_fields = ("book_code", "original_name", "relative_path", "sha256")
    list_filter = ("status", "preview_operation", "extension")


@admin.register(ManualTranslationJob)
class ManualTranslationJobAdmin(admin.ModelAdmin):
    list_display = (
        "job_id",
        "edition",
        "schema_version",
        "target_language",
        "status",
        "chapter_count",
        "updated_at",
    )
    search_fields = ("job_id", "edition__work__code", "drive_path", "expected_return_name")
    list_filter = ("schema_version", "status", "target_language", "translation_mode")


@admin.register(TranslationUnit)
class TranslationUnitAdmin(admin.ModelAdmin):
    list_display = ("translation_job", "unit_id", "sequence", "unit_type", "status", "retry_count")
    search_fields = ("translation_job__job_id", "heading", "input_filename", "expected_return_filename")
    list_filter = ("unit_type", "status")


@admin.register(TranslationJobEvent)
class TranslationJobEventAdmin(admin.ModelAdmin):
    list_display = (
        "translation_job",
        "unit",
        "operation",
        "previous_status",
        "new_status",
        "origin",
        "created_at",
    )
    search_fields = ("translation_job__job_id", "correlation_id", "operation")
    list_filter = ("operation", "new_status", "origin")


@admin.register(ProductionBookmark)
class ProductionBookmarkAdmin(admin.ModelAdmin):
    list_display = ("key", "edition", "target_language", "saved_at")
    search_fields = ("edition__work__code", "edition__work__title")
    readonly_fields = ("key", "edition", "target_language", "saved_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(IntakeAuditEvent)
class IntakeAuditEventAdmin(admin.ModelAdmin):
    list_display = ("batch", "item", "operation", "previous_status", "new_status", "attempt", "created_at")
    search_fields = ("correlation_id", "batch__batch_code", "item__book_code")
    list_filter = ("operation", "new_status")


admin.site.register(IntakeCounter)
