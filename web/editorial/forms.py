from django import forms

from pipeline.models import BookEditionTemplate


class FrontmatterTemplateForm(forms.ModelForm):
    introduction_text = forms.CharField(
        required=False,
        label="Introdução",
        help_text="Deixe vazio para remover essa seção do export.",
        widget=forms.Textarea(attrs={"rows": 10}),
    )
    about_edition_text = forms.CharField(
        required=False,
        label="Sobre esta edição",
        help_text="Deixe vazio para remover essa seção do export.",
        widget=forms.Textarea(attrs={"rows": 10}),
    )
    epilogue_text = forms.CharField(
        required=False,
        label="Epílogo",
        help_text="Deixe vazio para remover essa seção do export.",
        widget=forms.Textarea(attrs={"rows": 10}),
    )

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
            "about_contributor_text": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, **kwargs):
        self.edition = kwargs.pop("edition", None)
        super().__init__(*args, **kwargs)
        self.fields["book_code"].disabled = True
        self.fields["language"].disabled = True
        if self.edition is not None:
            self.fields["introduction_text"].initial = self.edition.introduction_text or ""
            self.fields["epilogue_text"].initial = self.edition.epilogue_text or ""
