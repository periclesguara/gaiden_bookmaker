from django import forms

from pipeline.models import BookEditionTemplate

BLANK_MARKERS = {"blank", "[blank]", "{blank}", "__blank__"}


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
            "has_preface",
            "preface_text",
            "has_introduction",
            "introduction_text",
            "has_epilogue",
            "epilogue_text",
            "about_contributor_text",
        ]
        widgets = {
            "frontispiece_text": forms.Textarea(attrs={"rows": 8}),
            "copyright_text": forms.Textarea(attrs={"rows": 14}),
            "about_edition_text": forms.Textarea(attrs={"rows": 8}),
            "preface_text": forms.Textarea(attrs={"rows": 8}),
            "introduction_text": forms.Textarea(attrs={"rows": 8}),
            "epilogue_text": forms.Textarea(attrs={"rows": 8}),
            "about_contributor_text": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["book_code"].disabled = True
        self.fields["language"].disabled = True

    @staticmethod
    def _normalize_blank(value: str) -> str:
        raw = (value or "").strip()
        if not raw or raw.lower() in BLANK_MARKERS:
            return ""
        return value

    def clean(self):
        cleaned = super().clean()
        for field in (
            "frontispiece_text",
            "copyright_text",
            "about_edition_text",
            "preface_text",
            "introduction_text",
            "epilogue_text",
            "about_contributor_text",
        ):
            cleaned[field] = self._normalize_blank(cleaned.get(field, ""))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            # Keep intentional blank sections as blank (don't auto-reseed defaults on save).
            instance.save(apply_defaults=False)
        return instance
