import re

from django import forms
from django.core.exceptions import ValidationError

from .models import BookEditionTemplate, LANGUAGE_DEFAULT_TEMPLATES


_BOOK_CODE_INPUT_RE = re.compile(r"^\s*(?:book[_-]?)?(\d+)\s*$", re.IGNORECASE)


def normalize_book_code_input(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    match = _BOOK_CODE_INPUT_RE.match(raw)
    if not match:
        return raw.lower()
    raw_digits = match.group(1)
    digits = raw_digits.lstrip("0") or "0"
    width = max(3, len(raw_digits))
    return f"book_{digits.zfill(width)}"


class BookEditionTemplateForm(forms.ModelForm):
    collaborator_pseudonym = forms.CharField(
        required=False,
        label="Pseudonimo",
    )

    collaborator_roles = forms.MultipleChoiceField(
        choices=BookEditionTemplate.ROLE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Roles for this contributor",
    )

    class Meta:
        model = BookEditionTemplate
        fields = [
            "book_code",
            "language",
            "title",
            "subtitle",
            "author_name",
            "publication_year",
            "work_kind",
            "original_publication_date",
            "original_author_death_date",
            "imprint_name",
            "collection_name",
            "collaborator_name",
            "collaborator_pseudonym",
            "collaborator_roles",
            "seal_name",
            "editor_name",
            "translator_name",
            "adapter_name",
            "frontispiece_text",
            "copyright_text",
            "about_edition_text",
            "about_contributor_text",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["work_kind"].label = "Tipo da obra"
        self.fields["work_kind"].required = False
        self.fields["work_kind"].initial = BookEditionTemplate.WORK_KIND_AUTHORIAL
        self.fields["original_publication_date"].label = "Data da publicacao original"
        self.fields["original_author_death_date"].label = "Data de falecimento do autor original"
        self.fields["original_publication_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["original_author_death_date"].widget = forms.DateInput(attrs={"type": "date"})

        if self.instance and self.instance.pk and self.instance.collaborator_roles:
            self.initial["collaborator_roles"] = self.instance.roles_list

        if (not self.instance or not self.instance.pk) and not self.is_bound:
            lang = self.initial.get("language") or self.instance.language or self.fields["language"].initial
            self._set_default_templates(lang)

    def _set_default_templates(self, lang: str):
        defaults = LANGUAGE_DEFAULT_TEMPLATES.get(lang)
        if defaults:
            if not self.initial.get("frontispiece_text"):
                self.fields["frontispiece_text"].initial = defaults["frontispiece_text"]
            if not self.initial.get("copyright_text"):
                self.fields["copyright_text"].initial = defaults["copyright_text"]

        # About fields are always manual and should not be auto-filled.

    def clean_collaborator_roles(self):
        roles = self.cleaned_data.get("collaborator_roles") or []
        return ",".join(roles)

    def clean_book_code(self):
        return normalize_book_code_input(self.cleaned_data.get("book_code", ""))

    def clean(self):
        cleaned_data = super().clean()
        work_kind = cleaned_data.get("work_kind")
        author_name = (cleaned_data.get("author_name") or "").strip()
        original_publication_date = cleaned_data.get("original_publication_date")
        original_author_death_date = cleaned_data.get("original_author_death_date")

        if work_kind == BookEditionTemplate.WORK_KIND_PUBLIC_DOMAIN:
            if not author_name:
                self.add_error("author_name", "Autor original e obrigatorio para obra de dominio publico.")
            if not original_publication_date:
                self.add_error(
                    "original_publication_date",
                    "Data da publicacao original e obrigatoria para obra de dominio publico.",
                )
            if not original_author_death_date:
                self.add_error(
                    "original_author_death_date",
                    "Data de falecimento do autor original e obrigatoria para obra de dominio publico.",
                )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.collaborator_roles = self.cleaned_data.get("collaborator_roles", "")
        if commit:
            instance.save()
        return instance


class BookSourceUploadForm(forms.Form):
    SOURCE_FORMAT_CHOICES = [
        ("txt", "TXT"),
        ("html", "HTML"),
        ("epub", "EPUB"),
    ]

    source_format = forms.ChoiceField(
        choices=SOURCE_FORMAT_CHOICES,
        widget=forms.RadioSelect,
        label="Tipo do arquivo-fonte",
    )
    source_file = forms.FileField(
        label="Arquivo-fonte",
        help_text="Envie um arquivo TXT, HTML ou EPUB conforme o tipo selecionado.",
    )
    replace_existing = forms.BooleanField(
        required=False,
        label="Substituir arquivo-fonte atual",
    )

    def __init__(self, *args, **kwargs):
        self.has_existing_source = kwargs.pop("has_existing_source", False)
        self.allowed_extensions_getter = kwargs.pop("allowed_extensions_getter", None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        source_format = (cleaned_data.get("source_format") or "").strip().lower()
        source_file = cleaned_data.get("source_file")
        replace_existing = bool(cleaned_data.get("replace_existing"))

        if self.has_existing_source and source_file and not replace_existing:
            raise ValidationError(
                "Ja existe um arquivo-fonte ativo. Marque a opcao de substituicao para enviar outro arquivo."
            )

        if source_file and self.allowed_extensions_getter is not None:
            allowed_exts = self.allowed_extensions_getter(source_format)
            uploaded_ext = normalize_upload_ext(source_file.name)
            if uploaded_ext not in allowed_exts:
                allowed_exts_label = ", ".join(sorted(allowed_exts))
                raise ValidationError(
                    f"Arquivo invalido para '{source_format}'. Aceitos: {allowed_exts_label}."
                )

        return cleaned_data


class ManualTranslationUploadForm(forms.Form):
    translated_file = forms.FileField(
        label="Arquivo traduzido",
        help_text="Selecione o TXT ou Markdown retornado pelo agente de tradução.",
        widget=forms.ClearableFileInput(attrs={"accept": ".txt,.md,text/plain,text/markdown"}),
    )

    def clean_translated_file(self):
        uploaded = self.cleaned_data["translated_file"]
        extension = normalize_upload_ext(uploaded.name)
        if extension not in {".txt", ".md"}:
            raise ValidationError("Envie um arquivo .txt ou .md.")
        if uploaded.size > 100 * 1024 * 1024:
            raise ValidationError("O arquivo traduzido excede 100 MB.")
        return uploaded


def normalize_upload_ext(filename: str) -> str:
    return f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
