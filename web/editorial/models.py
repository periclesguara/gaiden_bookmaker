import hashlib
from pathlib import Path

from django.db import models

from gaiden.infrastructure import storage


class Language(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)
    native_name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "language"

    def __str__(self) -> str:
        return f"{self.native_name} ({self.code})"


class Seal(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "seal"

    def __str__(self) -> str:
        return self.name


class ContributorRole(models.TextChoices):
    AUTHOR = "AUTHOR", "Autor"
    TRANSLATOR = "TRANSLATOR", "Tradutor"
    EDITOR = "EDITOR", "Editor"
    ADAPTER = "ADAPTER", "Adaptador"


class Contributor(models.Model):
    name = models.CharField(max_length=200)
    role = models.CharField(
        max_length=20,
        choices=ContributorRole.choices,
        default=ContributorRole.AUTHOR,
    )

    class Meta:
        db_table = "contributor"

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"


class Work(models.Model):
    code = models.SlugField(unique=True)
    title = models.CharField(max_length=255)
    original_language = models.ForeignKey(
        Language,
        on_delete=models.PROTECT,
        related_name="works_original",
    )
    author = models.ForeignKey(
        Contributor,
        on_delete=models.PROTECT,
        related_name="works_as_author",
    )
    publisher = models.CharField(max_length=255, blank=True)
    year = models.IntegerField(null=True, blank=True)
    is_public_domain = models.BooleanField(default=True)

    class Meta:
        db_table = "work"

    def __str__(self) -> str:
        return f"{self.title} ({self.author.name})"


class Edition(models.Model):
    LANGUAGE_CHOICES = [
        ("en", "English"),
        ("pt-br", "Português (Brasil)"),
        ("es", "Español"),
        ("de", "Deutsch"),
        ("it", "Italiano"),
        ("fr", "Français"),
    ]

    IMPRINT_CHOICES = [
        ("RinoBooks", "RinoBooks"),
        ("Wrecked Alien Machine", "Wrecked Alien Machine"),
    ]

    SEAL_CHOICES = [
        ("MantaQuest", "MantaQuest"),
        ("Wrecked Alien Machine", "Wrecked Alien Machine"),
    ]

    COUNTRY_CHOICES = [
        ("Brasil", "Brasil"),
        ("Brazil", "Brazil"),
        ("Brazile", "Brazile"),
    ]

    work = models.ForeignKey(
        Work,
        on_delete=models.CASCADE,
        related_name="editions",
    )
    language = models.ForeignKey(
        Language,
        on_delete=models.PROTECT,
        related_name="editions",
    )
    seal = models.ForeignKey(
        Seal,
        on_delete=models.PROTECT,
        related_name="editions",
    )
    main_contributor = models.ForeignKey(
        Contributor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="editions_main",
    )
    publisher = models.CharField(max_length=255, blank=True)
    edition_year = models.IntegerField(null=True, blank=True)
    raw_source_path = models.CharField(max_length=500, blank=True)
    title = models.CharField(max_length=255, blank=True)
    subtitle = models.CharField(max_length=255, blank=True)
    author = models.CharField(max_length=255, blank=True)
    adapter = models.CharField(max_length=255, blank=True)
    translator = models.CharField(max_length=255, blank=True)
    editor = models.CharField(max_length=255, blank=True)
    about_edition_text = models.TextField(blank=True)
    publication_year = models.IntegerField(default=2026)
    city = models.CharField(max_length=100, default="Rio de Janeiro")
    country = models.CharField(
        max_length=100,
        choices=COUNTRY_CHOICES,
        default="Brasil",
    )
    imprint_name = models.CharField(
        max_length=100,
        choices=IMPRINT_CHOICES,
        default="RinoBooks",
    )
    seal_name = models.CharField(
        max_length=100,
        choices=SEAL_CHOICES,
        default="MantaQuest",
    )
    frontispiece_template = models.TextField(
        default=(
            "{title}\n"
            "by {author}\n"
            "\n"
            "Modern {language} Edition\n"
            "adapted by {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city}, {country} · {year}"
        )
    )
    copyright_template = models.TextField(
        default=(
            "Title: {title}\n"
            "\n"
            "Subtitle: {subtitle}\n"
            "\n"
            "Author: {author}\n"
            "\n"
            "Adapter: {adapter}\n"
            "\n"
            "Editor: {editor}\n"
            "\n"
            "Publication Year: {year}\n"
            "\n"
            "The original work, *{title}* by {author},\n"
            "is in the public domain worldwide.\n"
            "\n"
            "Copyright © {year} RinoBooks.\n"
            "\n"
            "This modern edition, including translation, adaptation,\n"
            "and editorial material, is copyrighted by RinoBooks.\n"
            "\n"
            "This edition of *{title}*\n"
            "was produced under the {imprint} imprint.\n"
            "\n"
            "{imprint} is a registered trademark of RinoBooks.\n"
            "\n"
            "Publisher:\n"
            "{imprint}\n"
            "\n"
            "All rights reserved.\n"
            "\n"
            "{city}, {country} — {year}\n"
        )
    )
    about_edition_template = models.TextField(blank=True)
    about_contributor_template = models.TextField(blank=True)
    cover_filepath = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Path inside the project (e.g., data/covers/book_0001/en/cover.jpg).",
    )
    language_code = models.CharField(
        max_length=10,
        choices=LANGUAGE_CHOICES,
        default="en",
    )
    lock_translate = models.BooleanField(default=False)
    lock_refine = models.BooleanField(default=False)
    lock_polish = models.BooleanField(default=False)
    miolo_source_stage = models.CharField(
        max_length=20,
        blank=True,
        default="",
        choices=[
            ("", "AUTO"),
            ("translate", "translate"),
            ("refine", "refine"),
            ("polish", "polish"),
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "edition"
        unique_together = [("work", "language", "seal")]

    def __str__(self) -> str:
        return f"{self.work.title} [{self.language.code} · {self.seal.slug}]"


class PipelineStage(models.TextChoices):
    RAW = "RAW", "Original (raw)"
    NORMALIZED = "NORMALIZED", "Normalizado"
    SPLIT = "SPLIT", "Split"
    CHUNKED = "CHUNKED", "Chunked"
    TRANSLATED = "TRANSLATED", "Traduzido"
    REFINED = "REFINED", "Refine"
    MERGED = "MERGED", "Merge"
    POLISHED = "POLISHED", "Polish (Codex)"
    MIOLO_MD = "MIOLO_MD", "Miolo MD"
    FINAL_MD = "FINAL_MD", "MD Final"
    DONE = "DONE", "Finalizado"


class EditionPipeline(models.Model):
    edition = models.OneToOneField(
        Edition,
        on_delete=models.CASCADE,
        related_name="pipeline",
    )
    core_last_txt_path = models.CharField(max_length=500, blank=True, null=True)
    md_language = models.CharField(max_length=10, blank=True, null=True)
    frontmatter_language = models.CharField(max_length=10, blank=True, null=True)
    frontmatter_locked = models.BooleanField(default=False)
    current_stage = models.CharField(
        max_length=20,
        choices=PipelineStage.choices,
        default=PipelineStage.RAW,
    )
    translation_language = models.CharField(max_length=10, blank=True)
    refine_profile = models.CharField(max_length=30, blank=True, default="ingles_neutro")
    raw_at = models.DateTimeField(null=True, blank=True)
    normalized_at = models.DateTimeField(null=True, blank=True)
    split_at = models.DateTimeField(null=True, blank=True)
    chunked_at = models.DateTimeField(null=True, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)
    refined_at = models.DateTimeField(null=True, blank=True)
    merged_at = models.DateTimeField(null=True, blank=True)
    polished_at = models.DateTimeField(null=True, blank=True)
    miolo_md_at = models.DateTimeField(null=True, blank=True)
    final_md_at = models.DateTimeField(null=True, blank=True)
    editorial_changed = models.BooleanField(default=False)
    build_outdated = models.BooleanField(default=False)
    last_editorial_update_at = models.DateTimeField(null=True, blank=True)
    last_built_at = models.DateTimeField(null=True, blank=True)
    last_version_path = models.CharField(max_length=500, blank=True, default="")
    last_version_filename = models.CharField(max_length=255, blank=True, default="")
    last_log = models.TextField(blank=True)

    class Meta:
        db_table = "edition_pipeline"

    def __str__(self) -> str:
        return f"Pipeline({self.edition}) - {self.current_stage}"


class EditionBuild(models.Model):
    BUILD_TYPE_INITIAL = "initial"
    BUILD_TYPE_REBUILD = "rebuild"
    BUILD_TYPE_CHOICES = [
        (BUILD_TYPE_INITIAL, "Initial"),
        (BUILD_TYPE_REBUILD, "Rebuild"),
    ]
    STATUS_NOT_STARTED = "NOT_STARTED"
    STATUS_IN_PROGRESS = "IN_PROGRESS"
    STATUS_VALIDATING = "VALIDATING"
    STATUS_READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    STATUS_DONE = "DONE"
    STATUS_FAILED = "FAILED"
    STATUS_OUTDATED = "OUTDATED"
    STATUS_CHOICES = [
        (STATUS_NOT_STARTED, "Not started"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_VALIDATING, "Validating"),
        (STATUS_READY_FOR_APPROVAL, "Ready for approval"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
        (STATUS_OUTDATED, "Outdated"),
    ]

    edition = models.ForeignKey(
        Edition,
        on_delete=models.CASCADE,
        related_name="build_history",
    )
    language_code = models.CharField(max_length=10, db_index=True)
    build_version = models.IntegerField()
    build_type = models.CharField(max_length=20, choices=BUILD_TYPE_CHOICES, default=BUILD_TYPE_INITIAL)
    build_path = models.CharField(max_length=500, blank=True, default="")
    epub_path = models.CharField(max_length=500, blank=True, default="")
    pdf_path = models.CharField(max_length=500, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    locale = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_NOT_STARTED)
    artifact_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    artifact_size_bytes = models.PositiveBigIntegerField(default=0)
    artifact_source = models.CharField(max_length=50, blank=True, default="")
    is_final = models.BooleanField(default=False)
    validation_passed = models.BooleanField(default=False)
    official_body_path = models.CharField(max_length=500, blank=True, default="")
    official_body_sha256 = models.CharField(max_length=64, blank=True, default="")
    validated_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    validation_report = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "edition_build"
        unique_together = [("edition", "language_code", "build_version")]
        ordering = ["-build_version", "-created_at"]

    def __str__(self) -> str:
        return f"Build({self.edition} [{self.language_code}] v{self.build_version})"

    @property
    def epub_filename(self) -> str:
        return Path(self.epub_path).name if self.epub_path else ""

    @property
    def qualifies_as_done(self) -> bool:
        metadata_ready = bool(
            self.status == self.STATUS_DONE
            and self.is_final
            and self.validation_passed
            and self.epub_path
            and self.artifact_sha256
            and self.artifact_size_bytes
            and self.artifact_source
            and self.official_body_path
            and self.official_body_sha256
            and self.validated_at
            and self.approved_at
            and self.completed_at
        )
        return metadata_ready and not self.integrity_errors()

    @staticmethod
    def _stream_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_existing_path(value: str, *, require_epub: bool = False) -> Path | None:
        if not value:
            return None
        candidate = Path(value).expanduser()
        candidate = candidate.resolve() if candidate.is_absolute() else storage.resolve_repo_path(candidate).resolve()
        allowed_roots = (storage.storage_root().resolve(), storage.repo_root().resolve())
        if not any(candidate.is_relative_to(root) for root in allowed_roots):
            return None
        if not candidate.is_file() or (require_epub and candidate.suffix.casefold() != ".epub"):
            return None
        return candidate

    def integrity_errors(self) -> list[str]:
        errors: list[str] = []
        epub = self._safe_existing_path(self.epub_path, require_epub=True)
        body = self._safe_existing_path(self.official_body_path)
        if epub is None:
            errors.append("final EPUB is missing, outside canonical storage, or has an invalid extension")
        else:
            if epub.stat().st_size != self.artifact_size_bytes:
                errors.append("final EPUB size differs from the registered size")
            elif self._stream_sha256(epub) != self.artifact_sha256:
                errors.append("final EPUB SHA-256 differs from the registered hash")
        if body is None:
            errors.append("official body is missing or outside canonical storage")
        elif self._stream_sha256(body) != self.official_body_sha256:
            errors.append("official body SHA-256 differs from the registered hash")
        return errors


class EditionBuildAuditEvent(models.Model):
    build = models.ForeignKey(EditionBuild, on_delete=models.PROTECT, related_name="audit_events")
    event_type = models.CharField(max_length=50, db_index=True)
    actor = models.CharField(max_length=150, blank=True, default="system")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "edition_build_audit_event"
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.build_id}:{self.event_type}"


class EditionText(models.Model):
    edition = models.OneToOneField(
        Edition,
        on_delete=models.CASCADE,
        related_name="texts",
    )
    raw_text = models.TextField(blank=True)
    normalized_text = models.TextField(blank=True)
    raw_path = models.CharField(max_length=500, blank=True)
    normalized_path = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "edition_text"

    def __str__(self) -> str:
        return f"Texts({self.edition})"


class PipelineArtifact(models.Model):
    STAGE_CHOICES = [
        ("raw", "RAW"),
        ("normalize", "NORMALIZE"),
        ("split", "SPLIT/CHUNK"),
        ("translate", "TRANSLATE"),
        ("refine", "REFINE"),
        ("polish", "POLISH"),
        ("miolo", "MIOLO"),
        ("frontmatter", "FRONTMATTER"),
        ("build", "BUILD"),
        ("epub", "EPUB"),
        ("pdf", "PDF"),
        ("cover", "COVER"),
    ]

    work_code = models.CharField(max_length=200, db_index=True)
    language_code = models.CharField(max_length=20, db_index=True)
    stage = models.CharField(max_length=50, choices=STAGE_CHOICES, db_index=True)
    relpath = models.TextField()
    filename = models.CharField(max_length=255, db_index=True)
    size_bytes = models.BigIntegerField(default=0)
    mtime_iso = models.CharField(max_length=40, default="")
    exists = models.BooleanField(default=True)
    is_candidate = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("work_code", "language_code", "stage", "relpath")

    def __str__(self) -> str:
        return f"{self.work_code} [{self.language_code}] {self.stage}: {self.filename}"

# Create your models here.
