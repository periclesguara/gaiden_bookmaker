from django import forms

from gaiden.application.author_studio.create_author import create_author
from gaiden.application.author_studio.create_work import create_work
from gaiden.domain.author_studio.exceptions import AuthorStudioError


class AuthorCreateForm(forms.Form):
    name = forms.CharField(max_length=255, label="Nome do autor")

    def save(self):
        try:
            return create_author(self.cleaned_data["name"])
        except AuthorStudioError as exc:
            raise forms.ValidationError(str(exc)) from exc


class WorkCreateForm(forms.Form):
    title = forms.CharField(max_length=500, label="Nome da obra")
    original_language = forms.CharField(max_length=20, required=False, label="Idioma original")
    source_file = forms.FileField(label="Arquivo")

    def save(self, *, author):
        work = create_work(
            author=author,
            title=self.cleaned_data["title"],
            original_language=self.cleaned_data["original_language"],
        )
        return work, self.cleaned_data["source_file"]


class WorkEditForm(forms.Form):
    title = forms.CharField(max_length=500, label="Nome da obra")
    original_language = forms.CharField(max_length=20, required=False, label="Idioma original")
