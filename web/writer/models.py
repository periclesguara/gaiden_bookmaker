from django.db import models

class WriterStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    IN_REVIEW = "IN_REVIEW", "In review"
    APPROVED = "APPROVED", "Approved"
    PROMOTED = "PROMOTED", "Promoted"


class Manuscript(models.Model):
    work = models.OneToOneField("author_studio.Work", on_delete=models.PROTECT, related_name="writer_manuscript")
    status = models.CharField(max_length=20, choices=WriterStatus.choices, default=WriterStatus.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class EditorialWorkLink(models.Model):
    author_work = models.OneToOneField(
        "author_studio.Work", on_delete=models.PROTECT, related_name="editorial_identity_link"
    )
    editorial_work = models.OneToOneField(
        "editorial.Work", on_delete=models.PROTECT, related_name="writer_identity_link"
    )
    linked_by = models.CharField(max_length=150)
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.author_work_id} -> {self.editorial_work_id}"


class ManuscriptVersion(models.Model):
    manuscript = models.ForeignKey(Manuscript, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    content = models.TextField()
    sha256 = models.CharField(max_length=64, db_index=True)
    change_note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["manuscript", "version"], name="writer_unique_version")]


class WriterPromotionEvent(models.Model):
    manuscript = models.ForeignKey(Manuscript, on_delete=models.PROTECT, related_name="promotion_events")
    version = models.ForeignKey(ManuscriptVersion, on_delete=models.PROTECT, related_name="promotion_events")
    editor_approval = models.CharField(max_length=150)
    promoted_sha256 = models.CharField(max_length=64)
    previous_canonical_sha256 = models.CharField(max_length=64, blank=True, default="")
    previous_canonical_path = models.CharField(max_length=500, blank=True, default="")
    outcome = models.CharField(max_length=20, default="PROMOTED")
    actor = models.CharField(max_length=150, default="system")
    reason = models.CharField(max_length=500, blank=True, default="")
    new_canonical_path = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
