from django.db import models

from gaiden.domain.editorial.collections import COLLECTION_KIND_CHOICES, COLLECTION_STATUS_CHOICES, ITEM_STATUS_CHOICES


class Collection(models.Model):
    code = models.SlugField(unique=True)
    pipeline_book_code = models.SlugField(blank=True, default="")
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True)
    collection_kind = models.CharField(max_length=50, choices=COLLECTION_KIND_CHOICES)
    author_display_name = models.CharField(max_length=255)
    language = models.CharField(max_length=10)
    status = models.CharField(max_length=50, choices=COLLECTION_STATUS_CHOICES)
    item_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.code} - {self.title}"


class CollectionItem(models.Model):
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="items")
    order_index = models.PositiveIntegerField()
    author_name = models.CharField(max_length=255)
    work_title = models.CharField(max_length=255)
    source_filename = models.CharField(max_length=255, blank=True)
    source_original_path = models.CharField(max_length=500, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    upload_status = models.CharField(max_length=20, choices=ITEM_STATUS_CHOICES, default="pending")
    prep_status = models.CharField(max_length=20, choices=ITEM_STATUS_CHOICES, default="pending")
    normalize_status = models.CharField(max_length=20, choices=ITEM_STATUS_CHOICES, default="pending")
    merge_status = models.CharField(max_length=20, choices=ITEM_STATUS_CHOICES, default="pending")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order_index", "id"]
        unique_together = [("collection", "order_index"), ("collection", "author_name", "work_title")]

    def __str__(self) -> str:
        return f"{self.collection.code} #{self.order_index} {self.work_title}"


class CollectionArtifact(models.Model):
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="artifacts")
    artifact_type = models.CharField(max_length=50)
    language = models.CharField(max_length=10)
    path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CollectionRunState(models.Model):
    collection = models.OneToOneField(Collection, on_delete=models.CASCADE, related_name="run_state")
    current_step = models.CharField(max_length=50, blank=True, default="")
    last_error = models.TextField(blank=True)
    is_locked = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
