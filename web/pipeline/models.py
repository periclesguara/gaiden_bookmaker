from django.db import models


class PipelineJob(models.Model):
    STAGES = [
        ("raw", "Raw"),
        ("normalize", "Normalize"),
        ("split", "Split"),
        ("translate", "Translate"),
        ("refine", "Refine"),
        ("polish", "Polish"),
    ]

    STATUS = [
        ("PENDING", "Pendente"),
        ("RUNNING", "Rodando"),
        ("SUCCESS", "Sucesso"),
        ("FAIL", "Falhou"),
    ]

    book_code = models.CharField(max_length=50)
    book_title = models.CharField(max_length=255)
    language = models.CharField(max_length=10)
    stage = models.CharField(max_length=50, choices=STAGES)
    status = models.CharField(max_length=20, choices=STATUS, default="PENDING")
    filepath = models.TextField(blank=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["book_code", "language", "stage"]

    def __str__(self) -> str:
        return f"{self.book_code} [{self.language}] - {self.stage} ({self.status})"


class BookEditionTemplate(models.Model):
    LANG_EN = "en"
    LANG_PTBR = "ptbr"
    LANG_ES = "es"
    LANG_DE = "de"

    LANG_CHOICES = [
        (LANG_EN, "English"),
        (LANG_PTBR, "Portugues (Brasil)"),
        (LANG_ES, "Espanol"),
        (LANG_DE, "Deutsch"),
    ]

    ROLE_TRANSLATOR = "translator"
    ROLE_ADAPTER = "adapter"
    ROLE_CURATOR = "curator"
    ROLE_REVISOR = "revisor"
    ROLE_AUTHOR = "author"

    ROLE_CHOICES = [
        (ROLE_TRANSLATOR, "Translator"),
        (ROLE_ADAPTER, "Adapter"),
        (ROLE_CURATOR, "Curator"),
        (ROLE_REVISOR, "Revisor"),
        (ROLE_AUTHOR, "Author"),
    ]

    book_code = models.CharField(max_length=64, db_index=True)
    language = models.CharField(max_length=8, choices=LANG_CHOICES, default=LANG_EN)
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True)
    author_name = models.CharField(max_length=255)
    publication_year = models.IntegerField()
    imprint_name = models.CharField(max_length=255, blank=True)
    collection_name = models.CharField(max_length=255, blank=True)
    collaborator_name = models.CharField(max_length=255, blank=True)
    collaborator_pseudonym = models.CharField(max_length=255, blank=True)
    collaborator_roles = models.CharField(
        max_length=255,
        blank=True,
        help_text="Roles separated by comma: translator,adapter,curator,revisor,author",
    )
    cover_filepath = models.CharField(
        "Cover file path",
        max_length=500,
        blank=True,
        help_text="Path inside the project (e.g., data/covers/book_0001/en/cover.jpg).",
    )
    images_dir = models.CharField(
        "Images folder path",
        max_length=500,
        blank=True,
        help_text="Folder with book images (e.g., data/images/book_0001/en/).",
    )
    frontispiece_text = models.TextField(blank=True)
    copyright_text = models.TextField(blank=True)
    about_edition_text = models.TextField(blank=True)
    about_contributor_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("book_code", "language")
        ordering = ["book_code", "language"]

    def __str__(self) -> str:
        return f"{self.book_code} [{self.language}] - {self.title}"

    @property
    def roles_list(self):
        if not self.collaborator_roles:
            return []
        return [r.strip() for r in self.collaborator_roles.split(",") if r.strip()]

    @property
    def primary_role_label(self) -> str:
        mapping = {
            self.ROLE_TRANSLATOR: "Translator",
            self.ROLE_ADAPTER: "Adapter",
            self.ROLE_CURATOR: "Curator",
            self.ROLE_REVISOR: "Revisor",
            self.ROLE_AUTHOR: "Author",
        }
        roles = self.roles_list
        return mapping.get(roles[0], "Contributor") if roles else "Contributor"

    def get_placeholder_context(self) -> dict:
        return {
            "title": self.title,
            "author": self.author_name,
            "year": self.publication_year,
            "collaborator": self.collaborator_name,
            "pseudonym": self.collaborator_pseudonym,
            "role_label": self.primary_role_label,
        }

    def _render_text(self, raw_text: str) -> str:
        if not raw_text:
            return ""
        try:
            return raw_text.format(**self.get_placeholder_context())
        except Exception:
            return raw_text

    @property
    def frontispiece_rendered(self) -> str:
        return self._render_text(self.frontispiece_text)

    @property
    def copyright_rendered(self) -> str:
        return self._render_text(self.copyright_text)

    @property
    def about_edition_rendered(self) -> str:
        return self._render_text(self.about_edition_text)

    @property
    def about_contributor_rendered(self) -> str:
        return self._render_text(self.about_contributor_text)
