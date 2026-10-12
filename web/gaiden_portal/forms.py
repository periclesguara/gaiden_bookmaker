from django import forms

from editorial.models import Edition


class EditionForm(forms.ModelForm):
    COUNTRY_BY_LANGUAGE = {
        "en": "Brazil",
        "pt-br": "Brasil",
        "es": "Brasil",
        "de": "Brasilien",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        data_lang = None
        if self.data:
            data_lang = self.data.get("language_code")
        lang = data_lang or getattr(self.instance, "language_code", None) or "en"
        mapped_country = self.COUNTRY_BY_LANGUAGE.get(lang, "Brasil")
        self.fields["country"].choices = [(mapped_country, mapped_country)]
        self.fields["country"].help_text = "Fixo por idioma (manual depois)."
        if not self.initial.get("country"):
            self.initial["country"] = mapped_country

    def clean(self):
        cleaned = super().clean()
        lang = cleaned.get("language_code") or getattr(self.instance, "language_code", None) or "en"
        mapped_country = self.COUNTRY_BY_LANGUAGE.get(lang, "Brasil")
        cleaned["country"] = mapped_country
        return cleaned

    class Meta:
        model = Edition
        fields = [
            "title",
            "subtitle",
            "author",
            "adapter",
            "translator",
            "editor",
            "publisher",
            "about_edition_text",
            "publication_year",
            "city",
            "country",
            "imprint_name",
            "seal_name",
            "language_code",
            "frontispiece_template",
            "copyright_template",
            "about_edition_template",
            "about_contributor_template",
        ]
