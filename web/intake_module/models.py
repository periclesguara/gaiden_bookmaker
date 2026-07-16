from django.db import models

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


class IntakeItem(models.Model):
    batch = models.ForeignKey(IntakeBatch, on_delete=models.CASCADE, related_name="items")
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
        if not self.source_sha256:
            return False
        return self.batch.items.filter(source_sha256=self.source_sha256).exclude(pk=self.pk).exists()

    def __str__(self) -> str:
        return f"{self.batch.code} #{self.order_index}: {self.source_filename}"
