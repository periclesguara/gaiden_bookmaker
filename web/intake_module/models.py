import uuid

from django.db import models, transaction

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    def save(self, *args, **kwargs):
        if self.code:
            super().save(*args, **kwargs)
            return

        # Allocate the database identity first, then derive an immutable,
        # operator-friendly batch code from it. The provisional value stays
        # invisible to other transactions.
        with transaction.atomic():
            self.code = f"pending-{uuid.uuid4().hex}"
            super().save(*args, **kwargs)

            base = f"batch_{self.pk:04d}"
            candidate = base
            suffix = 2
            queryset = type(self).objects.exclude(pk=self.pk)
            while queryset.filter(code=candidate).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            type(self).objects.filter(pk=self.pk).update(code=candidate)
            self.code = candidate


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
            models.UniqueConstraint(fields=["batch", "order_index"], name="intake_unique_item_order_per_batch")
        ]

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of_id is not None

    def __str__(self) -> str:
        return f"{self.batch.code} #{self.order_index}: {self.source_filename}"
