from django import forms
from django.utils.text import slugify

from editorial.models import EditionMetadata
from pipeline.models import BookEditionTemplate


class FrontmatterTemplateForm(forms.ModelForm):
    class Meta:
        model = BookEditionTemplate
        fields = [
            "book_code",
            "language",
            "seal_name",
            "title",
            "subtitle",
            "author_name",
            "publication_year",
            "imprint_name",
            "city_name",
            "country_name",
            "editor_name",
            "translator_name",
            "adapter_name",
            "frontispiece_text",
            "copyright_text",
            "about_edition_text",
            "about_contributor_text",
        ]
        widgets = {
            "frontispiece_text": forms.Textarea(attrs={"rows": 8}),
            "copyright_text": forms.Textarea(attrs={"rows": 14}),
            "about_edition_text": forms.Textarea(attrs={"rows": 8}),
            "about_contributor_text": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["book_code"].disabled = True
        self.fields["language"].disabled = True


class EditionMetadataForm(forms.ModelForm):
    slug = forms.CharField(
        label="Slug",
        required=False,
        help_text="Será normalizado automaticamente para minúsculas e hífens.",
    )
    keywords = forms.CharField(
        label="Palavras-chave",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Uma palavra-chave por linha ou separadas por vírgula.",
    )
    promotional_images = forms.CharField(
        label="Imagens promocionais",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Uma URL ou caminho externo por linha. Os arquivos não são armazenados no Git.",
    )

    class Meta:
        model = EditionMetadata
        exclude = ["edition", "status", "validated_at", "created_at", "updated_at"]
        widgets = {
            "seo_description": forms.Textarea(attrs={"rows": 4}),
            "description": forms.Textarea(attrs={"rows": 7}),
            "short_description": forms.Textarea(attrs={"rows": 3}),
            "consulted_source": forms.Textarea(attrs={"rows": 3}),
            "legal_basis": forms.Textarea(attrs={"rows": 3}),
            "editorial_modifications": forms.Textarea(attrs={"rows": 4}),
            "authorized_territories": forms.Textarea(attrs={"rows": 3}),
            "blocked_territories": forms.Textarea(attrs={"rows": 3}),
            "rights_evidence": forms.Textarea(attrs={"rows": 4}),
            "sample_content": forms.Textarea(attrs={"rows": 7}),
            "expected_release_date": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "edition_code": "Código da edição",
            "commercial_title": "Título comercial",
            "subtitle": "Subtítulo",
            "original_title": "Título original",
            "author_first_name": "Nome do autor",
            "author_last_name": "Sobrenome do autor",
            "author_pseudonym": "Pseudônimo",
            "regional_language": "Idioma regional",
            "original_language": "Idioma original",
            "imprint_name": "Selo editorial",
            "collection_name": "Coleção",
            "edition_number": "Número da edição",
            "publication_year": "Ano de publicação",
            "edition_format": "Formato da edição",
            "seo_title": "Título SEO",
            "seo_description": "Descrição SEO",
            "description": "Descrição comercial completa",
            "short_description": "Descrição curta",
            "primary_category": "Categoria principal",
            "subcategory": "Subcategoria",
            "theme": "Tema",
            "target_audience": "Público-alvo",
            "cover_alt": "Texto alternativo da capa",
            "work_type": "Tipo da obra",
            "base_work_year": "Ano da obra-base",
            "consulted_source": "Fonte consultada",
            "legal_basis": "Base jurídica",
            "edition_nature": "Natureza da edição",
            "editorial_modifications": "Resumo das modificações editoriais",
            "authorized_territories": "Territórios autorizados",
            "blocked_territories": "Territórios bloqueados",
            "rights_evidence": "Evidências ou observações de direitos",
            "price": "Preço",
            "currency": "Moeda",
            "expected_release_date": "Data prevista de lançamento",
            "hotmart_url": "Link Hotmart",
            "lulu_url": "Link Lulu",
            "sample_title": "Título da amostra",
            "sample_content": "Conteúdo da amostra",
            "promotional_images": "Imagens promocionais",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
        if self.instance and self.instance.pk:
            self.initial["keywords"] = "\n".join(self.instance.keywords or [])
            self.initial["promotional_images"] = "\n".join(
                self.instance.promotional_images or []
            )

    @staticmethod
    def _split_list(value: str) -> list[str]:
        values = []
        for line in (value or "").splitlines():
            values.extend(part.strip() for part in line.split(","))
        return list(dict.fromkeys(value for value in values if value))

    def clean_slug(self):
        normalized = slugify(self.cleaned_data.get("slug") or "") or None
        if normalized:
            duplicate = EditionMetadata.objects.filter(slug=normalized)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError("Este slug já pertence a outra edição.")
        return normalized

    def clean_edition_code(self):
        code = (self.cleaned_data.get("edition_code") or "").strip().upper() or None
        if code:
            duplicate = EditionMetadata.objects.filter(edition_code=code)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError(
                    "Este código de edição já pertence a outra edição."
                )
        return code

    def clean_keywords(self):
        return self._split_list(self.cleaned_data.get("keywords") or "")

    def clean_promotional_images(self):
        return self._split_list(self.cleaned_data.get("promotional_images") or "")
