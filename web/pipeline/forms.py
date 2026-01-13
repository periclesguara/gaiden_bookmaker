from django import forms

from .models import BookEditionTemplate


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

        if not self.instance or not self.instance.pk:
            lang = self.initial.get("language") or self.fields["language"].initial
            self._set_default_templates(lang)

    def _set_default_templates(self, lang: str):
        if lang == BookEditionTemplate.LANG_EN:
            self.fields["frontispiece_text"].initial = (
                "This edition of {title} by {author} was prepared in {year}.\n"
            )
            self.fields["copyright_text"].initial = (
                "Copyright {year} {author}. All rights reserved.\n"
                "Adaptation and notes Copyright {year} {collaborator}.\n"
            )
            self.fields["about_edition_text"].initial = (
                "About this edition:\n"
                "This is a modernized, carefully revised edition prepared for contemporary readers."
            )
            self.fields["about_contributor_text"].initial = (
                "About the {role_label}:\n"
                "{collaborator} is responsible for the adaptation/translation and editorial curation of this volume."
            )
        elif lang == BookEditionTemplate.LANG_PTBR:
            self.fields["frontispiece_text"].initial = (
                "Esta edicao de {title}, de {author}, foi preparada em {year}.\n"
            )
            self.fields["copyright_text"].initial = (
                "Copyright {year} {author}. Todos os direitos reservados.\n"
                "Adaptacao e notas Copyright {year} {collaborator}.\n"
            )
            self.fields["about_edition_text"].initial = (
                "Sobre esta edicao:\n"
                "Edicao modernizada e revisada, pensada para o leitor contemporaneo."
            )
            self.fields["about_contributor_text"].initial = (
                "Sobre o(a) {role_label}:\n"
                "{collaborator} e responsavel pela adaptacao/traducao e curadoria editorial deste volume."
            )
        elif lang == BookEditionTemplate.LANG_ES:
            self.fields["frontispiece_text"].initial = (
                "Esta edicion de {title}, de {author}, fue preparada en {year}.\n"
            )
            self.fields["copyright_text"].initial = (
                "Copyright {year} {author}. Todos los derechos reservados.\n"
                "Adaptacion y notas Copyright {year} {collaborator}.\n"
            )
            self.fields["about_edition_text"].initial = (
                "Sobre esta edicion:\n"
                "Edicion modernizada y revisada para el lector contemporaneo."
            )
            self.fields["about_contributor_text"].initial = (
                "Sobre el/la {role_label}:\n"
                "{collaborator} es responsable de la adaptacion/traduccion y la curaduria editorial de este volumen."
            )
        elif lang == BookEditionTemplate.LANG_DE:
            self.fields["frontispiece_text"].initial = (
                "Diese Ausgabe von {title} von {author} wurde im Jahr {year} vorbereitet.\n"
            )
            self.fields["copyright_text"].initial = (
                "Copyright {year} {author}. Alle Rechte vorbehalten.\n"
                "Bearbeitung und Anmerkungen Copyright {year} {collaborator}.\n"
            )
            self.fields["about_edition_text"].initial = (
                "Ueber diese Ausgabe:\n"
                "Modernisierte, sorgfaeltig ueberarbeitete Ausgabe fuer heutige Leser."
            )
            self.fields["about_contributor_text"].initial = (
                "Ueber den/die {role_label}:\n"
                "{collaborator} ist verantwortlich fuer die Bearbeitung/Uebersetzung und redaktionelle Betreuung dieses Bandes."
            )

    def clean_collaborator_roles(self):
        roles = self.cleaned_data.get("collaborator_roles") or []
        return ",".join(roles)

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.collaborator_roles = self.cleaned_data.get("collaborator_roles", "")
        if commit:
            instance.save()
        return instance
