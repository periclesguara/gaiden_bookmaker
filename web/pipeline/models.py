from pathlib import Path

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
            "{city} · {year}"
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
            "Rio de Janeiro, Brazil — {year}"
        ),
    },
    "ptbr": {
        "frontispiece_text": (
            "{title}\n"
            "by {author}\n"
            "\n"
            "Modern {language} Edition\n"
            "adapted by {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city} · {year}"
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
            "Rio de Janeiro, Brazil — {year}"
        ),
    },
    "es": {
        "frontispiece_text": (
            "{title}\n"
            "by {author}\n"
            "\n"
            "Modern {language} Edition\n"
            "adapted by {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city} · {year}"
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
            "Rio de Janeiro, Brazil — {year}"
        ),
    },
    "de": {
        "frontispiece_text": (
            "{title}\n"
            "by {author}\n"
            "\n"
            "Modern {language} Edition\n"
            "adapted by {adapter}\n"
            "\n"
            "{imprint}\n"
            "{city} · {year}"
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
            "Rio de Janeiro, Brazil — {year}"
        ),
    },
}

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_book_md_path(book_code: str, language: str) -> Path:
    return (
        PROJECT_ROOT
        / "data"
        / "builds"
        / book_code
        / language
        / f"{book_code}_{language}_book.md"
    )


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
    text_source_mode = models.CharField(max_length=100, default="auto")
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
        language_map = {
            "en": "English",
            "ptbr": "Portuguese",
            "es": "Spanish",
            "de": "German",
        }
        language_label = language_map.get(self.language, (self.language or "").upper())
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "author": self.author_name,
            "year": self.publication_year,
            "collaborator": self.collaborator_name,
            "pseudonym": pseudonym,
            "imprint": self.imprint_name,
            "role_label": self.primary_role_label,
            "publisher": self.collaborator_name,
            "adapter": pseudonym,
            "language": language_label,
            "city": "Rio de Janeiro",
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
        updated_fields = self.apply_language_defaults_if_empty()
        if updated_fields and kwargs.get("update_fields") is not None:
            update_fields = set(kwargs["update_fields"])
            update_fields.update(updated_fields)
            kwargs["update_fields"] = update_fields
        super().save(*args, **kwargs)
