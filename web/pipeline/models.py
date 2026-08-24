from pathlib import Path
import uuid

from django.db import connection, models
from django.db.utils import OperationalError

from editorial.models import Edition as EditorialEdition


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


class TextSnapshot(models.Model):
    edition = models.ForeignKey(
        EditorialEdition,
        on_delete=models.CASCADE,
        related_name="text_snapshots",
    )
    language = models.CharField(max_length=10)
    stage = models.CharField(max_length=50)
    source_path = models.TextField(blank=True)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Snapshot({self.edition} [{self.language}] - {self.stage})"


LANGUAGE_DEFAULT_TEMPLATES = {
    "en": {
        "frontispiece_text": (
            "{title}\n"
            "by {author}\n"
            "\n"
            "Modern {language} Edition\n"
            "adapted by {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city}, {country} · {year}"
        ),
        "copyright_text": (
            "Title\n"
            "{title}\n"
            "Subtitle\n"
            "{subtitle}\n"
            "Author\n"
            "{author}\n"
            "Adapter\n"
            "{adapter}\n"
            "Publication year\n"
            "{year}\n"
            "\n"
            "Copyright © {year} Arthur Conan Doyle.\n"
            "Public Domain in the United States and other territories.\n"
            "\n"
            "This modern version of *{title}* was produced under the MantaQuest imprint.\n"
            "MantaQuest is a registered trademark of RinoBooks.\n"
            "\n"
            "Publisher: {publisher}\n"
            "All rights reserved to RinoBooks.\n"
            "{city}, {country} — {year}"
        ),
    },
    "ptbr": {
        "frontispiece_text": (
            "{title}\n"
            "por {author}\n"
            "\n"
            "Edicao moderna em {language}\n"
            "adaptado por {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city}, {country} · {year}"
        ),
        "copyright_text": (
            "Titulo\n"
            "{title}\n"
            "Subtitulo\n"
            "{subtitle}\n"
            "Autor\n"
            "{author}\n"
            "Adaptacao\n"
            "{adapter}\n"
            "Ano de publicacao\n"
            "{year}\n"
            "\n"
            "Copyright © {year} Arthur Conan Doyle.\n"
            "Dominio publico nos Estados Unidos e em outros territorios.\n"
            "\n"
            "Esta versao moderna de *{title}* foi produzida sob o selo MantaQuest.\n"
            "MantaQuest e uma marca registrada da RinoBooks.\n"
            "\n"
            "Editora: {publisher}\n"
            "Todos os direitos reservados a RinoBooks.\n"
            "{city}, {country} — {year}"
        ),
    },
    "es": {
        "frontispiece_text": (
            "{title}\n"
            "por {author}\n"
            "\n"
            "Edicion moderna en {language}\n"
            "adaptado por {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city}, {country} · {year}"
        ),
        "copyright_text": (
            "Titulo\n"
            "{title}\n"
            "Subtitulo\n"
            "{subtitle}\n"
            "Autor\n"
            "{author}\n"
            "Adaptacion\n"
            "{adapter}\n"
            "Ano de publicacion\n"
            "{year}\n"
            "\n"
            "Copyright © {year} Arthur Conan Doyle.\n"
            "Dominio publico en los Estados Unidos y otros territorios.\n"
            "\n"
            "Esta version moderna de *{title}* fue producida bajo el sello MantaQuest.\n"
            "MantaQuest es una marca registrada de RinoBooks.\n"
            "\n"
            "Editorial: {publisher}\n"
            "Todos los derechos reservados a RinoBooks.\n"
            "{city}, {country} — {year}"
        ),
    },
    "de": {
        "frontispiece_text": (
            "{title}\n"
            "von {author}\n"
            "\n"
            "Moderne {language}-Ausgabe\n"
            "bearbeitet von {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city}, {country} · {year}"
        ),
        "copyright_text": (
            "Titel\n"
            "{title}\n"
            "Untertitel\n"
            "{subtitle}\n"
            "Autor\n"
            "{author}\n"
            "Bearbeitung\n"
            "{adapter}\n"
            "Erscheinungsjahr\n"
            "{year}\n"
            "\n"
            "Copyright © {year} Arthur Conan Doyle.\n"
            "Gemeinfrei in den Vereinigten Staaten und anderen Gebieten.\n"
            "\n"
            "Diese moderne Ausgabe von *{title}* wurde unter dem MantaQuest-Imprint erstellt.\n"
            "MantaQuest ist eine eingetragene Marke von RinoBooks.\n"
            "\n"
            "Verlag: {publisher}\n"
            "Alle Rechte vorbehalten für RinoBooks.\n"
            "{city}, {country} — {year}"
        ),
    },
    "fr": {
        "frontispiece_text": (
            "{title}\n"
            "par {author}\n"
            "\n"
            "Edition moderne en {language}\n"
            "adaptation de {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city}, {country} · {year}"
        ),
        "copyright_text": (
            "Titre\n"
            "{title}\n"
            "Sous-titre\n"
            "{subtitle}\n"
            "Auteur\n"
            "{author}\n"
            "Adaptation\n"
            "{adapter}\n"
            "Annee de publication\n"
            "{year}\n"
            "\n"
            "Copyright © {year} Arthur Conan Doyle.\n"
            "Domaine public aux Etats-Unis et dans d'autres territoires.\n"
            "\n"
            "Cette edition moderne de *{title}* a ete produite sous l'empreinte MantaQuest.\n"
            "MantaQuest est une marque deposee de RinoBooks.\n"
            "\n"
            "Editeur: {publisher}\n"
            "Tous droits reserves a RinoBooks.\n"
            "{city}, {country} — {year}"
        ),
    },
    "it": {
        "frontispiece_text": (
            "{title}\n"
            "di {author}\n"
            "\n"
            "Edizione moderna in {language}\n"
            "adattamento di {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city}, {country} · {year}"
        ),
        "copyright_text": (
            "Titolo\n"
            "{title}\n"
            "Sottotitolo\n"
            "{subtitle}\n"
            "Autore\n"
            "{author}\n"
            "Adattamento\n"
            "{adapter}\n"
            "Anno di pubblicazione\n"
            "{year}\n"
            "\n"
            "Copyright © {year} Arthur Conan Doyle.\n"
            "Di pubblico dominio negli Stati Uniti e in altri territori.\n"
            "\n"
            "Questa edizione moderna di *{title}* e stata prodotta sotto il marchio MantaQuest.\n"
            "MantaQuest e un marchio registrato di RinoBooks.\n"
            "\n"
            "Editore: {publisher}\n"
            "Tutti i diritti riservati a RinoBooks.\n"
            "{city}, {country} — {year}"
        ),
    },
}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EDITORIAL_LANGUAGES = ("en", "ptbr", "es", "de", "it", "fr")
CORE_BLOCK_KEY = "bloco_02"
CORE_ISOLATION_LANGUAGES = EDITORIAL_LANGUAGES
SYSTEM_BLOCKS = (
    {
        "key": "bloco_01",
        "title": "Bloco 01 - Entrada",
        "description": "Cadastro, upload e preparo de entrada para o processamento.",
    },
    {
        "key": CORE_BLOCK_KEY,
        "title": "Bloco 02 - Core do Sistema",
        "description": "Normalize, fix, chunks, translate, split by chapter, refine e polish.",
    },
    {
        "key": "bloco_03",
        "title": "Bloco 03 - Editorial e Assets",
        "description": "Frontmatter, prefacio, introducao, epilogo, imagens e capa.",
    },
    {
        "key": "bloco_04",
        "title": "Bloco 04 - Finalizacao",
        "description": "Montagem final, build EPUB, build PDF e geracao final da edicao.",
    },
)


def get_book_md_path(book_code: str, language: str) -> Path:
    return (
        PROJECT_ROOT
        / "data"
        / "builds"
        / book_code
        / language
        / f"{book_code}_{language}_book.md"
    )


class BookEditionTemplateManager(models.Manager):
    def get_queryset(self):
        ensure_bookeditiontemplate_runtime_columns()
        return super().get_queryset()


class BookEditionTemplate(models.Model):
    WORK_KIND_AUTHORIAL = "AUTHORIAL"
    WORK_KIND_PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    WORK_KIND_CHOICES = [
        (WORK_KIND_AUTHORIAL, "Obra autoral"),
        (WORK_KIND_PUBLIC_DOMAIN, "Obra de dominio publico"),
    ]

    STATUS_DRAFT = "DRAFT"
    STATUS_REGISTERED = "REGISTERED"
    STATUS_READY_FOR_BLOCK_02 = "READY_FOR_BLOCK_02"
    REGISTRATION_STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_REGISTERED, "Registered"),
        (STATUS_READY_FOR_BLOCK_02, "Ready for Block 02"),
    ]

    LANG_EN = "en"
    LANG_PTBR = "ptbr"
    LANG_ES = "es"
    LANG_DE = "de"
    LANG_FR = "fr"
    LANG_IT = "it"

    LANG_CHOICES = [
        (LANG_EN, "en"),
        (LANG_ES, "es"),
        (LANG_PTBR, "pt-br"),
        (LANG_DE, "Deutsch"),
        (LANG_FR, "Français"),
        (LANG_IT, "Italiano"),
    ]

    ROLE_TRANSLATOR = "translator"
    ROLE_ADAPTER = "adapter"
    ROLE_CURATOR = "curator"
    ROLE_REVISOR = "revisor"
    ROLE_PUBLISHER = "publisher"
    ROLE_AUTHOR = "author"
    ROLE_PREFACE = "preface"
    ROLE_INTRODUCTION = "introduction"
    ROLE_EPILOGUE = "epilogue"
    ROLE_ILLUSTRATOR = "illustrator"

    ROLE_CHOICES = [
        (ROLE_TRANSLATOR, "Translator"),
        (ROLE_ADAPTER, "Adapter"),
        (ROLE_CURATOR, "Curator"),
        (ROLE_REVISOR, "Revisor"),
        (ROLE_PUBLISHER, "Publisher"),
        (ROLE_AUTHOR, "Author"),
        (ROLE_PREFACE, "Preface"),
        (ROLE_INTRODUCTION, "Introduction"),
        (ROLE_EPILOGUE, "Epilogue"),
        (ROLE_ILLUSTRATOR, "Illustrator"),
    ]

    book_code = models.CharField(max_length=64, db_index=True)
    language = models.CharField(max_length=8, choices=LANG_CHOICES, default=LANG_EN)
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True)
    author_name = models.CharField(max_length=255)
    publication_year = models.IntegerField()
    original_publication_date = models.DateField(null=True, blank=True)
    original_author_death_date = models.DateField(null=True, blank=True)
    work_kind = models.CharField(
        max_length=20,
        choices=WORK_KIND_CHOICES,
        default=WORK_KIND_AUTHORIAL,
    )
    imprint_name = models.CharField(max_length=255, blank=True)
    collection_name = models.CharField(max_length=255, blank=True)
    collaborator_name = models.CharField(max_length=255, blank=True)
    collaborator_pseudonym = models.CharField(max_length=255, blank=True)
    collaborator_roles = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Roles separated by comma: translator,adapter,curator,revisor,publisher,"
            "author,preface,introduction,epilogue,illustrator"
        ),
    )
    seal_name = models.CharField(max_length=255, blank=True)
    editor_name = models.CharField(max_length=255, blank=True)
    translator_name = models.CharField(max_length=255, blank=True)
    adapter_name = models.CharField(max_length=255, blank=True)
    city_name = models.CharField(max_length=255, blank=True)
    country_name = models.CharField(max_length=255, blank=True)
    editorial_name = models.CharField(max_length=120, blank=True, default="")
    edition_year = models.IntegerField(null=True, blank=True)
    edition_copyright_holder = models.CharField(max_length=120, blank=True, default="")
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
    has_preface = models.BooleanField(default=False)
    preface_text = models.TextField(blank=True)
    has_introduction = models.BooleanField(default=False)
    introduction_text = models.TextField(blank=True)
    has_epilogue = models.BooleanField(default=False)
    epilogue_text = models.TextField(blank=True)
    about_contributor_text = models.TextField(blank=True)
    text_source_mode = models.CharField(max_length=100, default="auto")
    registration_status = models.CharField(
        max_length=30,
        choices=REGISTRATION_STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    source_file_type = models.CharField(max_length=10, blank=True, default="")
    source_original_name = models.CharField(max_length=255, blank=True, default="")
    source_saved_path = models.CharField(max_length=500, blank=True, default="")
    source_file_size = models.BigIntegerField(null=True, blank=True)
    source_uploaded_at = models.DateTimeField(null=True, blank=True)
    source_file_sha256 = models.CharField(max_length=64, blank=True, default="")
    source_uploaded_by = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = BookEditionTemplateManager()

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
            self.ROLE_PUBLISHER: "Publisher",
            self.ROLE_AUTHOR: "Author",
            self.ROLE_PREFACE: "Preface",
            self.ROLE_INTRODUCTION: "Introduction",
            self.ROLE_EPILOGUE: "Epilogue",
            self.ROLE_ILLUSTRATOR: "Illustrator",
        }
        roles = self.roles_list
        return mapping.get(roles[0], "Contributor") if roles else "Contributor"

    def get_placeholder_context(self) -> dict:
        pseudonym = self.collaborator_pseudonym or self.collaborator_name
        adapter = self.adapter_name or pseudonym or self.collaborator_name
        imprint = self.seal_name or self.imprint_name
        language_map = {
            "en": "English",
            "ptbr": "Português",
            "es": "Español",
            "de": "Deutsch",
            "fr": "Français",
            "it": "Italiano",
        }
        language_label = language_map.get(self.language, (self.language or "").upper())
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "author": self.author_name,
            "year": self.publication_year,
            "collaborator": self.collaborator_name,
            "pseudonym": pseudonym,
            "imprint": imprint,
            "role_label": self.primary_role_label,
            "publisher": self.collaborator_name,
            "adapter": adapter,
            "translator": self.translator_name,
            "editor": self.editor_name,
            "seal": self.seal_name,
            "language": language_label,
            "city": self.city_name or "Rio de Janeiro",
            "country": self.country_name or "Brasil",
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
    def preface_rendered(self) -> str:
        return self._render_text(self.preface_text)

    @property
    def introduction_rendered(self) -> str:
        return self._render_text(self.introduction_text)

    @property
    def epilogue_rendered(self) -> str:
        return self._render_text(self.epilogue_text)

    @property
    def about_contributor_rendered(self) -> str:
        return self._render_text(self.about_contributor_text)

    def apply_language_defaults_if_empty(self):
        defaults = LANGUAGE_DEFAULT_TEMPLATES.get(self.language)
        if not defaults:
            return []

        def is_any_default(value: str, field_name: str) -> bool:
            return any(
                value == lang_defaults.get(field_name)
                for lang_defaults in LANGUAGE_DEFAULT_TEMPLATES.values()
            )

        updated_fields = []
        if not self.frontispiece_text or is_any_default(self.frontispiece_text, "frontispiece_text"):
            self.frontispiece_text = defaults["frontispiece_text"]
            updated_fields.append("frontispiece_text")
        if not self.copyright_text or is_any_default(self.copyright_text, "copyright_text"):
            self.copyright_text = defaults["copyright_text"]
            updated_fields.append("copyright_text")
        return updated_fields

    def save(self, *args, **kwargs):
        apply_defaults = kwargs.pop("apply_defaults", True)
        updated_fields = []
        if apply_defaults:
            updated_fields = self.apply_language_defaults_if_empty()
        if updated_fields and kwargs.get("update_fields") is not None:
            update_fields = set(kwargs["update_fields"])
            update_fields.update(updated_fields)
            kwargs["update_fields"] = update_fields
        ensure_bookeditiontemplate_runtime_columns()
        super().save(*args, **kwargs)


def ensure_bookeditiontemplate_runtime_columns() -> None:
    table_name = BookEditionTemplate._meta.db_table
    datetime_column_type = _runtime_datetime_column_type()
    columns_sql = {
        "original_publication_date": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN original_publication_date date NULL",
        "original_author_death_date": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN original_author_death_date date NULL",
        "work_kind": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN work_kind varchar(20) NOT NULL DEFAULT 'AUTHORIAL'",
        "registration_status": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN registration_status varchar(30) NOT NULL DEFAULT 'DRAFT'",
        "has_preface": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN has_preface boolean NOT NULL DEFAULT FALSE",
        "preface_text": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN preface_text text NOT NULL DEFAULT ''",
        "has_introduction": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN has_introduction boolean NOT NULL DEFAULT FALSE",
        "introduction_text": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN introduction_text text NOT NULL DEFAULT ''",
        "has_epilogue": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN has_epilogue boolean NOT NULL DEFAULT FALSE",
        "epilogue_text": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN epilogue_text text NOT NULL DEFAULT ''",
        "source_file_type": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN source_file_type varchar(10) NOT NULL DEFAULT ''",
        "source_original_name": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN source_original_name varchar(255) NOT NULL DEFAULT ''",
        "source_saved_path": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN source_saved_path varchar(500) NOT NULL DEFAULT ''",
        "source_file_size": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN source_file_size bigint NULL",
        "source_uploaded_at": f"ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN source_uploaded_at {datetime_column_type} NULL",
        "source_file_sha256": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN source_file_sha256 varchar(64) NOT NULL DEFAULT ''",
        "source_uploaded_by": "ALTER TABLE pipeline_bookeditiontemplate ADD COLUMN source_uploaded_by varchar(150) NOT NULL DEFAULT ''",
    }

    with connection.cursor() as cursor:
        table_names = set(connection.introspection.table_names(cursor))
        if table_name not in table_names:
            return
        description = connection.introspection.get_table_description(cursor, table_name)
        existing_columns = {getattr(col, "name", col[0]) for col in description}
        for column_name, sql in columns_sql.items():
            if column_name in existing_columns:
                continue
            cursor.execute(sql)


def _runtime_datetime_column_type(vendor: str | None = None) -> str:
    vendor = vendor or connection.vendor
    if vendor == "postgresql":
        return "timestamp with time zone"
    if vendor == "mysql":
        return "datetime(6)"
    return "datetime"


INCREMENTAL_EDITORIAL_STATUS = (
    ("DRAFT", "Draft"),
    ("READY", "Ready"),
    ("IMPORTED", "Imported"),
    ("IN_PROGRESS", "In progress"),
    ("RETURNED", "Returned"),
    ("APPROVED", "Approved"),
    ("FAILED", "Failed"),
    ("SUPERSEDED", "Superseded"),
)


class IncrementalEdition(models.Model):
    """Persistent resume cursor for one manifest-defined edition."""

    edition_id = models.CharField(max_length=255, unique=True)
    editorial_edition = models.ForeignKey(
        EditorialEdition,
        on_delete=models.SET_NULL,
        related_name="incremental_editions",
        null=True,
        blank=True,
    )
    work_id = models.CharField(max_length=255)
    book_code = models.CharField(max_length=64, db_index=True)
    locale = models.CharField(max_length=16)
    expected_block_count = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=INCREMENTAL_EDITORIAL_STATUS,
        default="DRAFT",
    )
    last_contiguous_sequence = models.PositiveIntegerField(default=0)
    next_sequence = models.PositiveIntegerField(null=True, blank=True)
    confirmed_block_id = models.CharField(max_length=255, blank=True, default="")
    manifest_sha256 = models.CharField(max_length=64, blank=True, default="")
    last_import_run_id = models.CharField(max_length=64, blank=True, default="")
    drive_destination = models.CharField(max_length=1000, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["edition_id"]

    def __str__(self) -> str:
        return self.edition_id


class IncrementalBlock(models.Model):
    """Immutable content version; prior versions remain queryable."""

    edition = models.ForeignKey(
        IncrementalEdition,
        on_delete=models.CASCADE,
        related_name="blocks",
    )
    block_id = models.CharField(max_length=500)
    sequence = models.PositiveIntegerField()
    version = models.PositiveIntegerField(default=1)
    file_name = models.CharField(max_length=500)
    content = models.TextField()
    content_sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    status = models.CharField(
        max_length=20,
        choices=INCREMENTAL_EDITORIAL_STATUS,
        default="IMPORTED",
    )
    source_block_id = models.CharField(max_length=500, blank=True, default="")
    source_updated_at = models.DateTimeField(null=True, blank=True)
    is_current = models.BooleanField(default=True)
    exported_sha256 = models.CharField(max_length=64, blank=True, default="")
    exported_status = models.CharField(max_length=20, blank=True, default="")
    exported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["edition_id", "sequence", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=("edition", "block_id", "version"),
                name="pipeline_incremental_block_version_unique",
            ),
            models.UniqueConstraint(
                fields=("edition", "sequence", "version"),
                name="pipeline_incremental_sequence_version_unique",
            ),
            models.UniqueConstraint(
                fields=("edition", "block_id"),
                condition=models.Q(is_current=True),
                name="pipeline_incremental_current_block_unique",
            ),
            models.UniqueConstraint(
                fields=("edition", "sequence"),
                condition=models.Q(is_current=True),
                name="pipeline_incremental_current_sequence_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.block_id}@{self.version}"


class IncrementalImportRun(models.Model):
    STATUS_CHOICES = (
        ("RUNNING", "Running"),
        ("SUCCESS", "Success"),
        ("PARTIAL", "Partial"),
        ("FAILED", "Failed"),
    )

    run_id = models.CharField(max_length=64, unique=True)
    edition = models.ForeignKey(
        IncrementalEdition,
        on_delete=models.CASCADE,
        related_name="import_runs",
    )
    job_id = models.CharField(max_length=255)
    manifest_sha256 = models.CharField(max_length=64)
    import_attempt = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RUNNING")
    manifest = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("job_id", "manifest_sha256", "import_attempt"),
                name="pipeline_incremental_import_idempotency_unique",
            )
        ]

    def __str__(self) -> str:
        return self.run_id


class IncrementalImportEvent(models.Model):
    run = models.ForeignKey(
        IncrementalImportRun,
        on_delete=models.CASCADE,
        related_name="events",
    )
    block_version = models.ForeignKey(
        IncrementalBlock,
        on_delete=models.SET_NULL,
        related_name="import_events",
        null=True,
        blank=True,
    )
    sequence = models.PositiveIntegerField()
    block_id = models.CharField(max_length=500)
    action = models.CharField(max_length=40)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["run_id", "sequence", "id"]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.sequence}:{self.action}"


INTAKE_STATUS_CHOICES = (
    ("DISCOVERED", "Discovered"),
    ("PREVIEWED", "Previewed"),
    ("STAGED", "Staged"),
    ("IMPORTED_RAW", "Imported raw"),
    ("REGISTERED", "Registered"),
    ("FAILED_RETRYABLE", "Failed — retryable"),
    ("CONFLICT", "Conflict"),
    ("REJECTED", "Rejected"),
)


class IntakeCounter(models.Model):
    """Database-locked allocator used for immutable batch and book codes."""

    key = models.CharField(max_length=32, unique=True)
    next_value = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]


class IntakeBatch(models.Model):
    SOURCE_CHOICES = (
        ("UPLOAD", "Upload"),
        ("GOOGLE_DRIVE", "Google Drive"),
        ("LOCAL_WATCH", "Local monitored folder"),
    )

    batch_code = models.CharField(max_length=32, unique=True, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    remote = models.CharField(max_length=100, blank=True, default="")
    drive_source_path = models.CharField(max_length=1000, blank=True, default="")
    recursive = models.BooleanField(default=True)
    defaults = models.JSONField(default=dict)
    status = models.CharField(max_length=24, choices=INTAKE_STATUS_CHOICES, default="DISCOVERED")
    last_error = models.TextField(blank=True, default="")
    last_summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("source", "remote", "drive_source_path"),
                name="pipeline_intake_batch_source_path_unique",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.only("batch_code").get(pk=self.pk)
            if original.batch_code != self.batch_code:
                raise ValueError("batch_code is immutable after confirmation.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.batch_code} — {self.name}"


class IntakeItem(models.Model):
    batch = models.ForeignKey(IntakeBatch, on_delete=models.CASCADE, related_name="items")
    remote_file_id = models.CharField(max_length=255, blank=True, default="")
    remote_path = models.CharField(max_length=1000)
    relative_path = models.CharField(max_length=1000)
    original_name = models.CharField(max_length=500)
    size_bytes = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=255, blank=True, default="")
    extension = models.CharField(max_length=20)
    remote_version = models.CharField(max_length=255, blank=True, default="")
    sha256 = models.CharField(max_length=64, blank=True, default="")
    title = models.CharField(max_length=500)
    author_name = models.CharField(max_length=255, blank=True, default="")
    source_language = models.CharField(max_length=16)
    target_language = models.CharField(max_length=16, blank=True, default="")
    book_code = models.CharField(max_length=64, blank=True, default="", editable=False)
    preview_operation = models.CharField(max_length=16)
    status = models.CharField(max_length=24, choices=INTAKE_STATUS_CHOICES, default="DISCOVERED")
    canonical_path = models.CharField(max_length=1200, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    attempt_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["batch_id", "relative_path"]
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "relative_path"),
                name="pipeline_intake_item_batch_path_unique",
            ),
            models.UniqueConstraint(
                fields=("batch", "remote_file_id"),
                condition=~models.Q(remote_file_id=""),
                name="pipeline_intake_item_batch_remote_unique",
            ),
            models.UniqueConstraint(
                fields=("book_code",),
                condition=~models.Q(book_code=""),
                name="pipeline_intake_item_book_code_unique",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.only("book_code").get(pk=self.pk)
            if original.book_code and original.book_code != self.book_code:
                raise ValueError("book_code is immutable after confirmation.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.batch.batch_code}:{self.book_code or self.relative_path}"


class IntakeAuditEvent(models.Model):
    batch = models.ForeignKey(IntakeBatch, on_delete=models.CASCADE, related_name="audit_events")
    item = models.ForeignKey(
        IntakeItem,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    correlation_id = models.CharField(max_length=64, db_index=True)
    operation = models.CharField(max_length=32)
    previous_status = models.CharField(max_length=24, blank=True, default="")
    new_status = models.CharField(max_length=24)
    attempt = models.PositiveIntegerField(default=1)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class ManualTranslationJob(models.Model):
    STATUS_EXPORTED = "EXPORTED"
    STATUS_IMPORTED = "IMPORTED"
    STATUS_FAILED = "FAILED"
    STATUS_SPLIT_PENDING = "SPLIT_PENDING"
    STATUS_SPLITTING = "SPLITTING"
    STATUS_SPLIT_REVIEW_REQUIRED = "SPLIT_REVIEW_REQUIRED"
    STATUS_SPLIT_VALIDATED = "SPLIT_VALIDATED"
    STATUS_DRIVE_EXPORTING = "DRIVE_EXPORTING"
    STATUS_DRIVE_READY = "DRIVE_READY"
    STATUS_TRANSLATION_IN_PROGRESS = "TRANSLATION_IN_PROGRESS"
    STATUS_PARTIAL_RETURN = "PARTIAL_RETURN"
    STATUS_RETURNS_READY = "RETURNS_READY"
    STATUS_VALIDATING_RETURNS = "VALIDATING_RETURNS"
    STATUS_MERGE_READY = "MERGE_READY"
    STATUS_MERGING = "MERGING"
    STATUS_MERGED = "MERGED"
    STATUS_VALIDATED = "VALIDATED"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_FAILED_RETRYABLE = "FAILED_RETRYABLE"
    STATUS_CONFLICT = "CONFLICT"
    STATUS_REJECTED = "REJECTED"
    STATUS_CHOICES = (
        (STATUS_EXPORTED, "Aguardando retorno"),
        (STATUS_IMPORTED, "Tradução importada"),
        (STATUS_FAILED, "Falha recuperável"),
        (STATUS_SPLIT_PENDING, "Split pendente"),
        (STATUS_SPLITTING, "Separando capítulos"),
        (STATUS_SPLIT_REVIEW_REQUIRED, "Split requer revisão"),
        (STATUS_SPLIT_VALIDATED, "Split validado"),
        (STATUS_DRIVE_EXPORTING, "Exportando ao Drive"),
        (STATUS_DRIVE_READY, "Drive pronto"),
        (STATUS_TRANSLATION_IN_PROGRESS, "Tradução em andamento"),
        (STATUS_PARTIAL_RETURN, "Retorno parcial"),
        (STATUS_RETURNS_READY, "Retornos prontos"),
        (STATUS_VALIDATING_RETURNS, "Validando retornos"),
        (STATUS_MERGE_READY, "Merge liberado"),
        (STATUS_MERGING, "Executando merge"),
        (STATUS_MERGED, "Merge concluído"),
        (STATUS_VALIDATED, "Manuscrito validado"),
        (STATUS_COMPLETED, "Tradução pronta"),
        (STATUS_FAILED_RETRYABLE, "Falha recuperável v2"),
        (STATUS_CONFLICT, "Conflito"),
        (STATUS_REJECTED, "Rejeitado"),
    )

    MODE_TRANSLATE = "translate"
    MODE_MODERNIZE_2026 = "modernize_2026"
    MODE_CHOICES = (
        (MODE_TRANSLATE, "Traduzir"),
        (MODE_MODERNIZE_2026, "Modernizar EN-US 2026"),
    )

    edition = models.ForeignKey(
        EditorialEdition,
        on_delete=models.CASCADE,
        related_name="manual_translation_jobs",
    )
    target_edition = models.ForeignKey(
        EditorialEdition,
        on_delete=models.SET_NULL,
        related_name="manual_translation_returns",
        null=True,
        blank=True,
    )
    job_id = models.CharField(max_length=160, blank=True, default="", editable=False)
    source_artifact = models.ForeignKey(
        "editorial.PipelineArtifact",
        on_delete=models.PROTECT,
        related_name="chapter_translation_sources",
        null=True,
        blank=True,
    )
    final_artifact = models.ForeignKey(
        "editorial.PipelineArtifact",
        on_delete=models.SET_NULL,
        related_name="chapter_translation_finals",
        null=True,
        blank=True,
    )
    source_language = models.CharField(max_length=16)
    target_language = models.CharField(max_length=16)
    translation_mode = models.CharField(max_length=24, choices=MODE_CHOICES, default=MODE_TRANSLATE)
    schema_version = models.CharField(max_length=64, default="gaiden_manual_translation_job_v1")
    splitter_version = models.CharField(max_length=64, blank=True, default="")
    split_strategy = models.CharField(max_length=32, blank=True, default="")
    chapter_count = models.PositiveIntegerField(default=0)
    split_manifest = models.JSONField(default=dict)
    validation_report = models.JSONField(default=dict)
    drive_path = models.CharField(max_length=1000)
    drive_root_folder_id = models.CharField(max_length=255, blank=True, default="")
    input_folder_id = models.CharField(max_length=255, blank=True, default="")
    return_folder_id = models.CharField(max_length=255, blank=True, default="")
    source_path = models.CharField(max_length=1200)
    source_sha256 = models.CharField(max_length=64)
    expected_return_name = models.CharField(max_length=500)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_EXPORTED)
    return_source = models.CharField(max_length=1200, blank=True, default="")
    return_sha256 = models.CharField(max_length=64, blank=True, default="")
    final_sha256 = models.CharField(max_length=64, blank=True, default="")
    correlation_id = models.CharField(max_length=64, default=uuid.uuid4, editable=False, db_index=True)
    last_error = models.TextField(blank=True, default="")
    exported_at = models.DateTimeField(auto_now_add=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=("edition", "target_language"),
                name="pipeline_manual_translation_edition_target_unique",
            ),
            models.UniqueConstraint(
                fields=("job_id",),
                condition=~models.Q(job_id=""),
                name="pipeline_manual_translation_job_id_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.edition.work.code} → {self.target_language} ({self.status})"


class TranslationUnit(models.Model):
    TYPE_PRELIMINARIES = "preliminaries"
    TYPE_PREFACE = "preface"
    TYPE_INTRODUCTION = "introduction"
    TYPE_CHAPTER = "chapter"
    TYPE_EPILOGUE = "epilogue"
    TYPE_APPENDIX = "appendix"
    TYPE_OVERSIZED_PART = "oversized_chapter_part"
    TYPE_CHOICES = (
        (TYPE_PRELIMINARIES, "Preliminares"),
        (TYPE_PREFACE, "Prefácio"),
        (TYPE_INTRODUCTION, "Introdução"),
        (TYPE_CHAPTER, "Capítulo"),
        (TYPE_EPILOGUE, "Epílogo"),
        (TYPE_APPENDIX, "Apêndice"),
        (TYPE_OVERSIZED_PART, "Parte de capítulo superdimensionado"),
    )
    STATUS_PENDING = "PENDING"
    STATUS_SPLIT = "SPLIT"
    STATUS_EXPORTED = "EXPORTED"
    STATUS_RETURNED = "RETURNED"
    STATUS_VALIDATED = "VALIDATED"
    STATUS_FAILED_RETRYABLE = "FAILED_RETRYABLE"
    STATUS_CONFLICT = "CONFLICT"
    STATUS_REJECTED = "REJECTED"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pendente"),
        (STATUS_SPLIT, "Separada"),
        (STATUS_EXPORTED, "Exportada"),
        (STATUS_RETURNED, "Retornada"),
        (STATUS_VALIDATED, "Validada"),
        (STATUS_FAILED_RETRYABLE, "Falha recuperável"),
        (STATUS_CONFLICT, "Conflito"),
        (STATUS_REJECTED, "Rejeitada"),
    )

    translation_job = models.ForeignKey(
        ManualTranslationJob,
        on_delete=models.CASCADE,
        related_name="units",
    )
    unit_id = models.CharField(max_length=32)
    sequence = models.PositiveIntegerField()
    unit_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    chapter_number = models.CharField(max_length=32, blank=True, default="")
    part_number = models.PositiveIntegerField(null=True, blank=True)
    heading = models.CharField(max_length=500, blank=True, default="")
    source_start_offset = models.PositiveBigIntegerField()
    source_end_offset = models.PositiveBigIntegerField()
    source_text_sha256 = models.CharField(max_length=64)
    source_size_bytes = models.PositiveBigIntegerField()
    input_filename = models.CharField(max_length=500)
    expected_return_filename = models.CharField(max_length=500)
    drive_input_file_id = models.CharField(max_length=255, blank=True, default="")
    drive_return_file_id = models.CharField(max_length=255, blank=True, default="")
    return_sha256 = models.CharField(max_length=64, blank=True, default="")
    return_size_bytes = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING)
    validation_report = models.JSONField(default=dict)
    retry_count = models.PositiveIntegerField(default=0)
    returned_at = models.DateTimeField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("translation_job_id", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("translation_job", "unit_id"),
                name="pipeline_translation_unit_job_unit_unique",
            ),
            models.UniqueConstraint(
                fields=("translation_job", "sequence"),
                name="pipeline_translation_unit_job_sequence_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.translation_job.job_id}:{self.unit_id}"


class TranslationJobEvent(models.Model):
    translation_job = models.ForeignKey(
        ManualTranslationJob,
        on_delete=models.CASCADE,
        related_name="events",
    )
    unit = models.ForeignKey(
        TranslationUnit,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    operation = models.CharField(max_length=48)
    previous_status = models.CharField(max_length=32, blank=True, default="")
    new_status = models.CharField(max_length=32)
    origin = models.CharField(max_length=32, default="gaiden")
    correlation_id = models.CharField(max_length=64, db_index=True)
    error = models.TextField(blank=True, default="")
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")


class ProductionBookmark(models.Model):
    """Append-only audit record used to resume editorial work safely."""

    key = models.CharField(max_length=64, unique=True, default=uuid.uuid4, editable=False)
    edition = models.ForeignKey(
        EditorialEdition,
        on_delete=models.PROTECT,
        related_name="production_bookmarks",
    )
    target_language = models.CharField(max_length=16, blank=True, default="")
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-saved_at"]

    def __str__(self) -> str:
        return f"{self.key}: {self.edition.work.code} ({self.target_language or self.edition.language.code})"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("ProductionBookmark é imutável; crie um novo registro de retomada.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("ProductionBookmark é imutável e não pode ser apagado.")
