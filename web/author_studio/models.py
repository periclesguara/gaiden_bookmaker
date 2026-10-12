from django.db import models

from gaiden.domain.author_studio.enums import (
    CanonicalTextStatus,
    SourceStatus,
    SplitOutcome,
    SplitRunStatus,
    SplitStatus,
    WorkStatus,
)
from gaiden.infrastructure.author_studio.storage import canonical_upload_path, chunk_upload_path, source_upload_path


def enum_choices(enum_type):
    return [(member.value, member.value.replace("_", " ").title()) for member in enum_type]


class Author(models.Model):
    name = models.CharField(max_length=255)
    canonical_name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True, db_index=True)
    code = models.CharField(max_length=12, unique=True, editable=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Work(models.Model):
    author = models.ForeignKey(Author, on_delete=models.PROTECT, related_name="works", db_index=True)
    title = models.CharField(max_length=500)
    canonical_title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=500)
    code = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    original_language = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(max_length=30, choices=enum_choices(WorkStatus), default=WorkStatus.CREATED.value)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(fields=["author", "canonical_title"], name="author_studio_unique_author_work"),
            models.UniqueConstraint(fields=["author", "slug"], name="author_studio_unique_author_slug"),
        ]

    def __str__(self):
        return f"{self.code} — {self.title}"


class WorkSource(models.Model):
    work = models.ForeignKey(Work, on_delete=models.PROTECT, related_name="sources")
    code = models.CharField(max_length=48, unique=True, editable=False, db_index=True)
    original_filename = models.CharField(max_length=500)
    stored_file = models.FileField(upload_to=source_upload_path)
    extension = models.CharField(max_length=20)
    mime_type = models.CharField(max_length=255, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True)
    source_provider = models.CharField(max_length=50, default="UNKNOWN")
    extraction_status = models.CharField(max_length=30, choices=enum_choices(SourceStatus), default=SourceStatus.PENDING.value)
    extraction_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["work", "sha256"], name="author_studio_unique_work_source")]

    def __str__(self):
        return self.code


class CanonicalText(models.Model):
    work = models.OneToOneField(Work, on_delete=models.PROTECT, related_name="canonical_text")
    source = models.ForeignKey(WorkSource, on_delete=models.PROTECT, related_name="canonical_versions")
    code = models.CharField(max_length=48, unique=True, editable=False, db_index=True)
    text_file = models.FileField(upload_to=canonical_upload_path)
    sha256 = models.CharField(max_length=64, db_index=True)
    character_count = models.PositiveBigIntegerField(default=0)
    word_count = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=30, choices=enum_choices(CanonicalTextStatus), default=CanonicalTextStatus.DRAFT.value)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.code


class WorkSplit(models.Model):
    work = models.OneToOneField(Work, on_delete=models.CASCADE, related_name="split_run")
    canonical_text = models.ForeignKey(CanonicalText, on_delete=models.CASCADE, related_name="split_runs")
    status = models.CharField(max_length=20, choices=enum_choices(SplitStatus), default=SplitStatus.PENDING.value)
    source_sha256 = models.CharField(max_length=64, db_index=True)
    chunker_version = models.CharField(max_length=50, blank=True)
    tokenizer_name = models.CharField(max_length=100, blank=True)
    minimum_tokens = models.PositiveIntegerField(default=400)
    target_tokens = models.PositiveIntegerField(default=700)
    maximum_tokens = models.PositiveIntegerField(default=900)
    overlap_tokens = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.work.code} — {self.status}"


class WorkChunk(models.Model):
    work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="chunks")
    canonical_text = models.ForeignKey(CanonicalText, on_delete=models.CASCADE, related_name="chunks")
    code = models.CharField(max_length=64, unique=True, editable=False, db_index=True)
    sequence = models.PositiveIntegerField()
    unit_type = models.CharField(max_length=30, blank=True)
    unit_title = models.CharField(max_length=500, blank=True)
    text_file = models.FileField(upload_to=chunk_upload_path)
    sha256 = models.CharField(max_length=64, db_index=True)
    character_count = models.PositiveIntegerField(default=0)
    word_count = models.PositiveIntegerField(default=0)
    estimated_tokens = models.PositiveIntegerField(default=0)
    token_count = models.PositiveIntegerField(default=0)
    tokenizer_name = models.CharField(max_length=100, blank=True)
    chunker_version = models.CharField(max_length=50, blank=True)
    start_line = models.PositiveIntegerField(default=0)
    end_line = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["work", "sequence"], name="author_studio_unique_work_chunk_sequence"),
        ]

    def __str__(self):
        return self.code


class WorkSplitRun(models.Model):
    work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="split_runs")
    canonical_text = models.ForeignKey(CanonicalText, on_delete=models.CASCADE, related_name="processing_runs")
    source_sha256 = models.CharField(max_length=64, db_index=True)
    chunker_version = models.CharField(max_length=50)
    tokenizer_name = models.CharField(max_length=100)
    minimum_tokens = models.PositiveIntegerField(default=400)
    target_tokens = models.PositiveIntegerField(default=700)
    maximum_tokens = models.PositiveIntegerField(default=900)
    overlap_tokens = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=enum_choices(SplitRunStatus),
        default=SplitRunStatus.PENDING.value,
    )
    outcome = models.CharField(max_length=30, choices=enum_choices(SplitOutcome), blank=True)
    chunks_previous = models.PositiveIntegerField(default=0)
    chunks_created = models.PositiveIntegerField(default=0)
    chunks_updated = models.PositiveIntegerField(default=0)
    chunks_preserved = models.PositiveIntegerField(default=0)
    chunks_removed = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"{self.work.code} — {self.outcome or self.status}"
