from django.db import models
from .trim_sizes import TRIM_CHOICES


class TypographyProfile(models.Model):
    name = models.CharField(max_length=100, unique=True)
    font = models.CharField(max_length=100, default="TeX Gyre Pagella")
    size_pt = models.DecimalField(max_digits=5, decimal_places=2, default=11)
    leading_pt = models.DecimalField(max_digits=5, decimal_places=2, default=14)
    license_reference = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return self.name


class PrintEdition(models.Model):
    PLATFORM_CHOICES = [
        ("ingramspark", "IngramSpark"),
        ("amazon_kdp", "Amazon KDP"),
        ("uiclap", "UICLAP"),
    ]
    BINDING_CHOICES = [
        ("paperback", "Paperback"),
        ("hardcover", "Hardcover"),
        ("hardcover_dust_jacket", "Hardcover com sobrecapa"),
    ]
    COVER_MODE_CHOICES = [
        ("new_print_cover", "Criar nova capa de impressão"),
        ("adapt_epub_cover", "Adaptar a capa atual do EPUB"),
        ("external_artwork", "Usar nova arte preparada externamente"),
    ]
    class State(models.TextChoices):
        DRAFT = "DRAFT", "Rascunho"
        CONFIGURED = "CONFIGURED", "Configurada"
        TYPESETTING = "TYPESETTING", "Compondo"
        INTERIOR_READY = "INTERIOR_READY", "Miolo pronto"
        COVER_PENDING = "COVER_PENDING", "Capa pendente"
        COVER_READY = "COVER_READY", "Capa pronta"
        PREFLIGHT_PENDING = "PREFLIGHT_PENDING", "Verificação pendente"
        PRINT_READY = "PRINT_READY", "Pronta para impressão"
        EXPORTED = "EXPORTED", "Exportada"
        INVALIDATED = "INVALIDATED", "Invalidada"

    source = models.ForeignKey("editorial.EditionBuild", on_delete=models.PROTECT)
    source_path = models.TextField()
    source_sha256 = models.CharField(max_length=64)
    name = models.CharField(max_length=150)
    platform = models.CharField(max_length=30, choices=PLATFORM_CHOICES, default="ingramspark")
    profile_version = models.CharField(max_length=40, default="2026-09-22")
    trim = models.CharField(max_length=20, choices=TRIM_CHOICES, default="6x9")
    paper = models.CharField(max_length=20, choices=[("white", "Branco"), ("cream", "Creme")], default="cream")
    binding = models.CharField(max_length=30, choices=BINDING_CHOICES, default="paperback")
    interior_color = models.CharField(max_length=30, default="black_and_white")
    typography = models.ForeignKey(TypographyProfile, on_delete=models.PROTECT)
    isbn = models.CharField(max_length=13, unique=True, null=True, blank=True)
    barcode_mode = models.CharField(
        max_length=20,
        choices=[("provider", "Gerado pela gráfica"), ("embedded", "Incorporado à arte")],
        default="provider",
    )
    cover_mode = models.CharField(
        max_length=30,
        choices=COVER_MODE_CHOICES,
        default="new_print_cover",
    )
    state = models.CharField(max_length=30, choices=State.choices, default=State.CONFIGURED)
    composition_settings = models.JSONField(default=dict, blank=True)
    cover_settings = models.JSONField(default=dict, blank=True)
    configuration_history = models.JSONField(default=list, blank=True)
    interior_approval = models.JSONField(default=dict, blank=True)
    cover_approval = models.JSONField(default=dict, blank=True)
    visual_review_approved_at = models.DateTimeField(null=True, blank=True)
    edition_approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ArtifactBuild(models.Model):
    edition = models.ForeignKey(PrintEdition, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=20, default="RUNNING")
    files = models.JSONField(default=dict)
    snapshot = models.JSONField(default=dict)
    tools = models.JSONField(default=dict)
    findings = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["edition", "version"], name="%(app_label)s_%(class)s_version")]


class InteriorBuild(ArtifactBuild):
    page_count = models.PositiveIntegerField(null=True)


class CoverBuild(ArtifactBuild):
    interior = models.ForeignKey(InteriorBuild, on_delete=models.PROTECT)
    geometry = models.JSONField(default=dict)


class PreflightReport(ArtifactBuild):
    interior = models.ForeignKey(InteriorBuild, on_delete=models.PROTECT)
    cover = models.ForeignKey(CoverBuild, on_delete=models.PROTECT)


class PrintExport(ArtifactBuild):
    report = models.ForeignKey(PreflightReport, on_delete=models.PROTECT)
