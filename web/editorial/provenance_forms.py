from django import forms

from gaiden.source_provenance import EDITABLE_FIELDS


class SourceProvenanceForm(forms.Form):
    original_title = forms.CharField(label="Título original", required=False)
    source_author = forms.CharField(label="Autoria original", required=False)
    original_publication_year = forms.IntegerField(label="Publicação original", required=False)
    original_publication_basis = forms.CharField(label="Fundamento da data", required=False)
    source_platform = forms.CharField(label="Plataforma-fonte", required=False)
    source_identifier = forms.CharField(label="ID da fonte", required=False)
    source_url = forms.URLField(label="URL da fonte", required=False)
    source_release_date = forms.CharField(label="Lançamento na fonte", required=False)
    source_credits = forms.CharField(
        label="Créditos da fonte",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    rights = forms.CharField(
        label="Direitos",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    source_language = forms.CharField(label="Idioma da fonte", required=False)
    subjects = forms.CharField(
        label="Assuntos",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Um assunto por linha.",
    )
    source_filename = forms.CharField(label="Arquivo-fonte", required=False, disabled=True)
    source_sha256 = forms.CharField(label="SHA-256", required=False, disabled=True)

    def __init__(self, *args, work=None, **kwargs):
        self.work = work
        provenance = dict(getattr(work, "source_provenance", {}) or {})
        initial = dict(kwargs.pop("initial", {}) or {})
        initial.update(provenance)
        subjects = initial.get("subjects")
        if isinstance(subjects, list):
            initial["subjects"] = "\n".join(subjects)
        super().__init__(*args, initial=initial, **kwargs)

    def save(self):
        if self.work is None:
            raise ValueError("SourceProvenanceForm requires a Work instance")
        current = dict(self.work.source_provenance or {})
        for field in EDITABLE_FIELDS:
            value = self.cleaned_data.get(field)
            if field == "subjects":
                value = [line.strip() for line in (value or "").splitlines() if line.strip()]
            if value in (None, "", []):
                current.pop(field, None)
            else:
                current[field] = value
        self.work.source_provenance = current
        self.work.save(update_fields=["source_provenance"])
        return self.work
