from django.db import models


class TypographyProfile(models.Model):
    name = models.CharField(max_length=100, unique=True)
    font = models.CharField(max_length=100, default="TeX Gyre Pagella")
    size_pt = models.DecimalField(max_digits=5, decimal_places=2, default=11)
    leading_pt = models.DecimalField(max_digits=5, decimal_places=2, default=14)
    license_reference = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return self.name


class PrintEdition(models.Model):
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
    platform = models.CharField(max_length=30, default="ingramspark")
    profile_version = models.CharField(max_length=40, default="2026-09-22")
    trim = models.CharField(max_length=20, choices=[("5.5x8.5", "5,5 × 8,5 in"), ("6x9", "6 × 9 in")], default="6x9")
    paper = models.CharField(max_length=20, choices=[("white", "Branco"), ("cream", "Creme")], default="cream")
    binding = models.CharField(max_length=20, default="paperback")
    interior_color = models.CharField(max_length=30, default="black_and_white")
    typography = models.ForeignKey(TypographyProfile, on_delete=models.PROTECT)
    isbn = models.CharField(max_length=13, unique=True, null=True, blank=True)
    barcode_mode = models.CharField(
        max_length=20,
        choices=[("provider", "Gerado pela gráfica"), ("embedded", "Incorporado à arte")],
        default="provider",
    )
    state = models.CharField(max_length=30, choices=State.choices, default=State.CONFIGURED)
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
