import uuid

from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from gaiden.domain.intake import IntakeState


STATUS_CHOICES = [(state.value, state.value.replace("_", " ").title()) for state in IntakeState]


class IntakeBatch(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=255)
    author_default = models.CharField(max_length=255, blank=True)
    source_language = models.CharField(max_length=20)
    imprint_default = models.CharField(max_length=255, blank=True)
    editor_default = models.CharField(max_length=255, blank=True)
    collection_name = models.CharField(max_length=255, blank=True)
    public_domain = models.BooleanField(default=False)
    drive_folder_id = models.CharField(max_length=255, blank=True)
    drive_relative_path = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=IntakeState.DISCOVERED.value)
    last_error = models.TextField(blank=True)
    book_codes_reserved_at = models.DateTimeField(null=True, blank=True)
    book_codes_reserved_by = models.CharField(max_length=150, blank=True)
    book_codes_start = models.SlugField(blank=True)
    book_codes_end = models.SlugField(blank=True)
    book_codes_allocated_count = models.PositiveIntegerField(default=0)
    book_code_plan_sha256 = models.CharField(max_length=64, blank=True)
    book_code_manifest = models.JSONField(default=dict, blank=True)
    book_code_manifest_projected_at = models.DateTimeField(null=True, blank=True)
    book_code_manifest_projection_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    def save(self, *args, **kwargs):
        if not self.code:
            folder_name = (self.drive_relative_path or "").strip().rstrip("/").rsplit("/", 1)[-1]
            base = slugify(folder_name or self.name) or "intake"
            max_length = self._meta.get_field("code").max_length
            base = base[:max_length]
            candidate = base
            suffix = 2
            queryset = type(self).objects.exclude(pk=self.pk)
            while queryset.filter(code=candidate).exists():
                marker = f"-{suffix}"
                candidate = f"{base[: max_length - len(marker)]}{marker}"
                suffix += 1
            self.code = candidate
        super().save(*args, **kwargs)


class IntakeItem(models.Model):
    batch = models.ForeignKey(IntakeBatch, on_delete=models.CASCADE, related_name="items")
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicate_items",
    )
    order_index = models.PositiveIntegerField()
    drive_file_id = models.CharField(max_length=255, blank=True)
    source_filename = models.CharField(max_length=255)
    source_format = models.CharField(max_length=10)
    source_size = models.PositiveBigIntegerField(default=0)
    source_sha256 = models.CharField(max_length=64, blank=True)
    suggested_title = models.CharField(max_length=255, blank=True)
    confirmed_title = models.CharField(max_length=255, blank=True)
    original_year = models.PositiveIntegerField(null=True, blank=True)
    target_language = models.CharField(max_length=20, blank=True)
    book_code = models.SlugField(blank=True)
    book_code_reserved_at = models.DateTimeField(null=True, blank=True)
    book_code_reserved_by = models.CharField(max_length=150, blank=True)
    original_path = models.CharField(max_length=500, blank=True)
    clean_path = models.CharField(max_length=500, blank=True)
    translation_input_path = models.CharField(max_length=500, blank=True)
    translation_return_path = models.CharField(max_length=500, blank=True)
    handoff_raw_path = models.CharField(max_length=500, blank=True)
    handoff_translated_path = models.CharField(max_length=500, blank=True)
    handoff_raw_sha256 = models.CharField(max_length=64, blank=True)
    handoff_translated_sha256 = models.CharField(max_length=64, blank=True)
    handoff_edition_id = models.PositiveBigIntegerField(null=True, blank=True)
    handed_off_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=IntakeState.DISCOVERED.value)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order_index", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "order_index"],
                name="intake_unique_item_order_per_batch",
            ),
            models.UniqueConstraint(
                fields=["book_code"],
                condition=~Q(book_code=""),
                name="intake_unique_nonempty_book_code",
            ),
            models.UniqueConstraint(
                fields=["handoff_edition_id"],
                condition=Q(handoff_edition_id__isnull=False),
                name="intake_unique_handoff_edition",
            ),
        ]

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of_id is not None

    def __str__(self) -> str:
        return f"{self.batch.code} #{self.order_index}: {self.source_filename}"


class BookCodeSequence(models.Model):
    name = models.SlugField(primary_key=True, default="book", editable=False)
    next_number = models.PositiveIntegerField(default=33)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "intake_book_code_sequence"

    def __str__(self) -> str:
        return f"{self.name}: {self.next_number}"


class TranslationJob(models.Model):
    STAGE_TRANSLATED = "translated"
    STAGE_OFFICIAL = "official"
    OUTPUT_STAGE_CHOICES = [
        (STAGE_TRANSLATED, "Translated intermediate"),
        (STAGE_OFFICIAL, "Editorial final"),
    ]

    STATUS_EXPORTED = "EXPORTED"
    STATUS_RETURN_PENDING = "RETURN_PENDING"
    STATUS_VALIDATED = "VALIDATED"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_SUPERSEDED = "SUPERSEDED"
    STATUS_FAILED = "FAILED"
    STATUS_CHOICES = [
        (STATUS_EXPORTED, "Exported"),
        (STATUS_RETURN_PENDING, "Return pending"),
        (STATUS_VALIDATED, "Validated"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_SUPERSEDED, "Superseded"),
        (STATUS_FAILED, "Failed"),
    ]

    job_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    edition = models.ForeignKey(
        "editorial.Edition",
        on_delete=models.PROTECT,
        related_name="translation_jobs",
    )
    intake_item = models.ForeignKey(
        IntakeItem,
        on_delete=models.PROTECT,
        related_name="translation_jobs",
    )
    schema_version = models.PositiveSmallIntegerField(default=2)
    source_language = models.CharField(max_length=10)
    target_language = models.CharField(max_length=10)
    output_stage = models.CharField(max_length=16, choices=OUTPUT_STAGE_CHOICES)
    confirmed_title = models.CharField(max_length=255)
    frozen_title_slug = models.SlugField(max_length=255)
    input_folder = models.CharField(max_length=500)
    input_filename = models.CharField(max_length=255)
    input_sha256 = models.CharField(max_length=64)
    expected_return_folder = models.CharField(max_length=500)
    expected_return_filename = models.CharField(max_length=255)
    manifest_filename = models.CharField(max_length=255)
    manifest_path = models.CharField(max_length=500)
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_EXPORTED,
    )
    return_sha256 = models.CharField(max_length=64, blank=True)
    validation_status = models.CharField(max_length=32, blank=True)
    validation_report_path = models.CharField(max_length=500, blank=True)
    warning_confirmed_at = models.DateTimeField(null=True, blank=True)
    warning_confirmed_by = models.CharField(max_length=255, blank=True)
    warning_confirmation_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "edition",
                    "intake_item",
                    "target_language",
                    "output_stage",
                    "input_sha256",
                ],
                name="intake_unique_translation_job_input",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.job_id} {self.edition_id} {self.target_language} {self.output_stage}"
