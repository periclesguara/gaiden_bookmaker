from django import forms

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
        ]
        widgets = {
            "frontispiece_text": forms.Textarea(attrs={"rows": 8}),
            "copyright_text": forms.Textarea(attrs={"rows": 14}),
            "about_edition_text": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["book_code"].disabled = True
        self.fields["language"].disabled = True
