from django import forms

from .models import BookEditionTemplate, LANGUAGE_DEFAULT_TEMPLATES


class BookEditionTemplateForm(forms.ModelForm):
    source_file = forms.FileField(
        required=False,
        label="Raw file (original text)",
        help_text="Select the source manuscript file to upload.",
    )
    cover_file = forms.FileField(
        required=False,
        label="Cover file",
        help_text="Upload a cover image (JPG/PNG).",
    )
    images_zip = forms.FileField(
        required=False,
        label="Images ZIP",
        help_text="Optional ZIP with interior images.",
    )

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
            "imprint_name",
            "collection_name",
            "collaborator_name",
            "collaborator_pseudonym",
            "collaborator_roles",
            "frontispiece_text",
            "copyright_text",
            "about_edition_text",
            "about_contributor_text",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.collaborator_roles = self.cleaned_data.get("collaborator_roles", "")
        if commit:
            instance.save()
        return instance
