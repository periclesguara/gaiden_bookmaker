from django.db import models


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
    current_stage = models.CharField(
        max_length=20,
        choices=PipelineStage.choices,
        default=PipelineStage.RAW,
    )
    translation_language = models.CharField(max_length=10, blank=True)
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
    last_log = models.TextField(blank=True)

    class Meta:
        db_table = "edition_pipeline"

    def __str__(self) -> str:
        return f"Pipeline({self.edition}) - {self.current_stage}"


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

# Create your models here.
