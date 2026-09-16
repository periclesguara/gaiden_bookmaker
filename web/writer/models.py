from __future__ import annotations

import hashlib

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SourceDocument(models.Model):
    class SourceType(models.TextChoices):
        LOCAL_FILE = "LOCAL_FILE", "Arquivo local"
        GOOGLE_DOC = "GOOGLE_DOC", "Google Docs"

    class Status(models.TextChoices):
        DISCOVERED = "DISCOVERED", "Descoberto"
        NORMALIZED = "NORMALIZED", "Normalizado"
        VECTORIZED = "VECTORIZED", "Vetorizado"
        FAILED = "FAILED", "Falhou"
        CONTRACT_READY = "CONTRACT_READY", "Contrato pronto"
        CONTRACT_FROZEN = "CONTRACT_FROZEN", "Contrato congelado"
        EXPORTED_TO_DRIVE = "EXPORTED_TO_DRIVE", "Exportado para Drive"
        WAITING_FOR_OUTPUT = "WAITING_FOR_OUTPUT", "Aguardando saída externa"
        OUTPUT_IMPORTED = "OUTPUT_IMPORTED", "Saída importada"
        CLOSURE_COMPLETE = "CLOSURE_COMPLETE", "Fechamento concluído"
        PACKAGE_READY = "PACKAGE_READY", "Pacote pronto localmente"

    filename = models.CharField(max_length=255)
    source_type = models.CharField(max_length=24, choices=SourceType.choices, default=SourceType.LOCAL_FILE)
    source_path = models.TextField(unique=False)
    external_id = models.CharField(max_length=255, blank=True, db_index=True)
    external_url = models.URLField(blank=True)
    external_revision_id = models.CharField(max_length=255, blank=True)
    external_modified_at = models.DateTimeField(null=True, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True)
    normalized_path = models.TextField(blank=True)
    normalized_sha256 = models.CharField(max_length=64, blank=True)
    provider = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DISCOVERED)
    normalization_report = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    discovered_at = models.DateTimeField(auto_now_add=True)
    normalized_at = models.DateTimeField(null=True, blank=True)
    vectorized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("filename", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_type", "external_id"),
                condition=Q(source_type="GOOGLE_DOC", external_id__gt=""),
                name="writer_unique_google_doc_source",
            )
        ]

    def __str__(self) -> str:
        return self.filename


class StoryProject(models.Model):
    title = models.CharField(max_length=255)
    language = models.CharField(max_length=40, default="Português (Brasil)")
    premise = models.TextField(blank=True)
    character_bible = models.TextField(blank=True)
    supporting_characters_bible = models.TextField(blank=True)
    antagonist_bible = models.TextField(blank=True)
    scenario_bible = models.TextField(blank=True)
    world_bible = models.TextField(blank=True)
    story_direction = models.TextField(blank=True)
    story_outline = models.TextField(blank=True)
    anchor_facts = models.JSONField(default=list, blank=True)
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


class Chapter(models.Model):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planejado"
        GENERATING = "GENERATING", "Gerando"
        MERGING = "MERGING", "Unindo cenas"
        MERGED = "MERGED", "Merge concluído"
        GENERATION_COMPLETE = "GENERATION_COMPLETE", "Geração concluída"
        FINAL = "FINAL", "Finalizado"
        FAILED = "FAILED", "Falhou"
        CONTRACT_READY = "CONTRACT_READY", "Contrato pronto"
        CONTRACT_FROZEN = "CONTRACT_FROZEN", "Contrato congelado"
        PACKAGE_READY = "PACKAGE_READY", "Pacote pronto localmente"
        EXPORTED_TO_DRIVE = "EXPORTED_TO_DRIVE", "Exportado para Drive"
        WAITING_FOR_OUTPUT = "WAITING_FOR_OUTPUT", "Aguardando saída externa"
        OUTPUT_IMPORTED = "OUTPUT_IMPORTED", "Saída importada"
        CLOSURE_COMPLETE = "CLOSURE_COMPLETE", "Fechamento concluído"

    project = models.ForeignKey(StoryProject, on_delete=models.CASCADE, related_name="chapters")
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    title = models.CharField(max_length=255, blank=True)
    direction = models.TextField(blank=True)
    script = models.TextField(blank=True)
    session_scenes = models.JSONField(default=list, blank=True)
    target_words = models.PositiveIntegerField(
        default=2500, validators=[MinValueValidator(400), MaxValueValidator(12000)]
    )
    session_count = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(4)]
    )
    retrieval_top_k = models.PositiveSmallIntegerField(
        default=8, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PLANNED)
    merged_text = models.TextField(blank=True)
    merge_model = models.CharField(max_length=160, blank=True)
    merge_input_sha256 = models.CharField(max_length=64, blank=True)
    merged_at = models.DateTimeField(null=True, blank=True)
    narrative_state = models.JSONField(default=dict, blank=True)
    narrative_memory_model = models.CharField(max_length=160, blank=True)
    logic_audit = models.JSONField(default=dict, blank=True)
    logic_audit_model = models.CharField(max_length=160, blank=True)
    memory_updated_at = models.DateTimeField(null=True, blank=True)
    final_text = models.TextField(blank=True)
    generated_text = models.TextField(blank=True)
    generation_model = models.CharField(max_length=160, blank=True)
    generation_parameters = models.JSONField(default=dict, blank=True)
    generation_source_chunk_ids = models.JSONField(default=list, blank=True)
    generation_source_scores = models.JSONField(default=list, blank=True)
    generation_contract_sha256 = models.CharField(max_length=64, blank=True)
    generation_metadata = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    contract_version = models.PositiveIntegerField(null=True, blank=True)
    contract_json_path = models.TextField(blank=True)
    drive_folder_url = models.URLField(blank=True)
    drive_contract_url = models.URLField(blank=True)
    drive_output_url = models.URLField(blank=True)
    drive_folder_id = models.CharField(max_length=255, blank=True)
    drive_contract_id = models.CharField(max_length=255, blank=True)
    drive_json_id = models.CharField(max_length=255, blank=True)
    drive_output_id = models.CharField(max_length=255, blank=True)
    drive_export_error = models.TextField(blank=True)
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
        return self.target_words

    @property
    def generated_word_count(self) -> int:
        return len(self.generated_text.split())

    @property
    def qa_ready(self) -> bool:
        return bool(self.generated_text.strip())

    def finalize(self) -> None:
        if not self.generated_text.strip():
            raise ValueError("chapter must contain generated text before finalization")
        self.final_text = self.generated_text.strip()
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
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("chapter_id", "number")
        constraints = [
            models.UniqueConstraint(fields=("chapter", "number"), name="writer_unique_chapter_session")
        ]

    @property
    def qa_passed(self) -> bool:
        params = self.generation_parameters
        qa = params.get("validation", {})
        return (params.get("qa_version") == 1 and qa.get("passed") is True
                and qa.get("scope") == "mechanical_and_local_critic"
                and params.get("qa_sha256") == hashlib.sha256(self.content.encode()).hexdigest())

    def __str__(self) -> str:
        return f"{self.chapter} — Sessão {self.number}"


class NarrativeContract(models.Model):
    class Status(models.TextChoices):
        READY = "READY", "Pronto"
        USED = "USED", "Usado"
        SUPERSEDED = "SUPERSEDED", "Substituído"

    project = models.ForeignKey(StoryProject, on_delete=models.CASCADE, related_name="narrative_contracts")
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="narrative_contracts")
    version = models.PositiveIntegerField(default=1)
    schema_version = models.CharField(max_length=20, default="1.0")
    book_kit_snapshot = models.JSONField(default=dict)
    book_kit_sha256 = models.CharField(max_length=64)
    chapter_plan = models.JSONField(default=dict)
    continuity_snapshot = models.JSONField(default=dict)
    chapter_history = models.JSONField(default=dict)
    source_rag_context = models.JSONField(default=list)
    narrative_memory_context = models.JSONField(default=list)
    payload_json = models.JSONField(default=dict)
    payload_sha256 = models.CharField(max_length=64)
    estimated_input_tokens = models.PositiveIntegerField(default=0)
    token_budget = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY)
    created_at = models.DateTimeField(auto_now_add=True)
    frozen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("project", "chapter", "version"), name="writer_unique_contract_version")]
        ordering = ("project_id", "chapter_id", "-version")


class ChapterNarrativeSummary(models.Model):
    project = models.ForeignKey(StoryProject, on_delete=models.CASCADE, related_name="chapter_summaries")
    chapter = models.OneToOneField(Chapter, on_delete=models.CASCADE, related_name="narrative_summary")
    version = models.PositiveIntegerField(default=1)
    short_summary = models.TextField(blank=True)
    detailed_summary = models.TextField(blank=True)
    opening_state = models.JSONField(default=dict)
    major_events = models.JSONField(default=list)
    closing_state = models.JSONField(default=dict)
    characters_present = models.JSONField(default=list)
    locations = models.JSONField(default=list)
    facts_established = models.JSONField(default=list)
    new_information = models.JSONField(default=list)
    resolved_threads = models.JSONField(default=list)
    open_threads = models.JSONField(default=list)
    continuity_delta = models.JSONField(default=dict)
    source_contract_sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class StoryContinuityState(models.Model):
    project = models.ForeignKey(StoryProject, on_delete=models.CASCADE, related_name="continuity_states")
    through_chapter = models.PositiveIntegerField()
    version = models.PositiveIntegerField(default=1)
    state_json = models.JSONField(default=dict)
    state_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("project", "through_chapter", "version"), name="writer_unique_continuity_version")]
        ordering = ("project_id", "-through_chapter", "-version")


class WriterStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    IN_REVIEW = "IN_REVIEW", "In review"
    APPROVED = "APPROVED", "Approved"
    PROMOTED = "PROMOTED", "Promoted"


class Manuscript(models.Model):
    work = models.OneToOneField(
        "author_studio.Work",
        on_delete=models.PROTECT,
        related_name="writer_manuscript",
    )
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
