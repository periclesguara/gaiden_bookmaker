from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from writer.language_contract import default_language_contract, validate_language_contract


class SourceDocument(models.Model):
    class Status(models.TextChoices):
        DISCOVERED = "DISCOVERED", "Descoberto"
        NORMALIZED = "NORMALIZED", "Normalizado"
        VECTORIZED = "VECTORIZED", "Vetorizado"
        FAILED = "FAILED", "Falhou"

    filename = models.CharField(max_length=255)
    source_path = models.TextField(unique=True)
    source_sha256 = models.CharField(max_length=64, blank=True)
    normalized_path = models.TextField(blank=True)
    normalized_sha256 = models.CharField(max_length=64, blank=True)
    provider = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DISCOVERED)
    normalization_report = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    discovered_at = models.DateTimeField(auto_now_add=True)
    normalized_at = models.DateTimeField(null=True, blank=True)
    vectorized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("filename", "id")

    def __str__(self) -> str:
        return self.filename


class StoryProject(models.Model):
    class WritingMode(models.TextChoices):
        FICTION = "FICTION", "Fiction — Ficção"
        NONFICTION = "NONFICTION", "Nonfiction — Não ficção"

    class Language(models.TextChoices):
        EN_US = "en-US", "EN-US — Inglês americano"
        EN_GB = "en-GB", "EN-UK — Inglês britânico"
        PT_BR = "pt-BR", "PT-BR — Português brasileiro"

    title = models.CharField(max_length=255)
    writing_mode = models.CharField(
        max_length=16,
        choices=WritingMode.choices,
        default=WritingMode.FICTION,
    )
    language = models.CharField(
        max_length=40,
        choices=Language.choices,
        default=Language.EN_US,
    )
    language_contract = models.JSONField(
        default=default_language_contract, validators=[validate_language_contract]
    )
    premise = models.TextField(blank=True)
    character_bible = models.TextField(blank=True)
    antagonist_bible = models.TextField(blank=True)
    supporting_characters_bible = models.TextField(blank=True)
    scenario_bible = models.TextField(blank=True)
    world_bible = models.TextField(blank=True)
    story_direction = models.TextField(blank=True)
    story_outline = models.TextField(blank=True)
    chapter_count = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    vector_index_path = models.TextField(blank=True)
    sources = models.ManyToManyField(SourceDocument, blank=True, related_name="projects")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "title")

    def __str__(self) -> str:
        return self.title


class SupportingCastRevision(models.Model):
    project = models.ForeignKey(
        StoryProject,
        on_delete=models.CASCADE,
        related_name="supporting_cast_revisions",
    )
    version = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    instruction = models.TextField()
    registry = models.JSONField(default=dict)
    registry_sha256 = models.CharField(max_length=64)
    source_chunk_ids = models.JSONField(default=list, blank=True)
    source_scores = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="writer_supporting_cast_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("project_id", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("project", "version"),
                name="writer_unique_supporting_cast_revision",
            )
        ]

    def __str__(self) -> str:
        return f"{self.project.title} — Coadjuvantes v{self.version}"


class Chapter(models.Model):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planejado"
        GENERATING = "GENERATING", "Gerando"
        GENERATION_COMPLETE = "GENERATION_COMPLETE", "Geração concluída"
        FINAL = "FINAL", "Finalizado"
        FAILED = "FAILED", "Falhou"

    project = models.ForeignKey(StoryProject, on_delete=models.CASCADE, related_name="chapters")
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    title = models.CharField(max_length=255, blank=True)
    direction = models.TextField(blank=True)
    script = models.TextField(blank=True)
    source_guidance = models.TextField(blank=True)
    target_words = models.PositiveIntegerField(
        default=2500, validators=[MinValueValidator(400), MaxValueValidator(12000)]
    )
    session_count = models.PositiveSmallIntegerField(
        default=4, validators=[MinValueValidator(1), MaxValueValidator(4)]
    )
    retrieval_top_k = models.PositiveSmallIntegerField(
        default=8, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PLANNED)
    final_text = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("project_id", "number")
        constraints = [
            models.UniqueConstraint(fields=("project", "number"), name="writer_unique_project_chapter")
        ]

    @property
    def estimated_tokens(self) -> int:
        return max(1, round(self.target_words * 1.45))

    @property
    def words_per_session(self) -> int:
        return max(1, round(self.target_words / self.session_count))

    def finalize(self) -> None:
        sessions = list(self.sessions.order_by("number"))
        if len(sessions) != self.session_count or any(
            session.status != ChapterSession.Status.COMPLETE for session in sessions
        ):
            raise ValueError("all configured sessions must be complete before finalization")
        self.final_text = "\n\n".join(session.content.strip() for session in sessions).strip()
        self.status = self.Status.FINAL
        self.finalized_at = timezone.now()
        self.error_message = ""
        self.save(update_fields=("final_text", "status", "finalized_at", "error_message", "updated_at"))

    def __str__(self) -> str:
        return f"{self.project.title} — Capítulo {self.number:02d}"


class ChapterSession(models.Model):
    class Status(models.TextChoices):
        COMPLETE = "COMPLETE", "Concluída"
        FAILED = "FAILED", "Falhou"

    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="sessions")
    number = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(4)]
    )
    status = models.CharField(max_length=12, choices=Status.choices)
    content = models.TextField(blank=True)
    word_count = models.PositiveIntegerField(default=0)
    model = models.CharField(max_length=160, blank=True)
    source_chunk_ids = models.JSONField(default=list, blank=True)
    source_scores = models.JSONField(default=list, blank=True)
    generation_parameters = models.JSONField(default=dict, blank=True)
    language_contract = models.JSONField(default=dict)
    language_contract_sha256 = models.CharField(max_length=64, blank=True)
    supporting_cast_revision = models.ForeignKey(
        SupportingCastRevision,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sessions",
    )
    supporting_cast_snapshot = models.JSONField(default=dict, blank=True)
    supporting_cast_sha256 = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("chapter_id", "number")
        constraints = [
            models.UniqueConstraint(fields=("chapter", "number"), name="writer_unique_chapter_session")
        ]

    def __str__(self) -> str:
        return f"{self.chapter} — Sessão {self.number}"
