from django import forms

from .models import IntakeBatch


class IntakeBatchForm(forms.ModelForm):
    class Meta:
        model = IntakeBatch
        fields = [
            "code",
            "name",
            "author_default",
            "source_language",
            "imprint_default",
            "editor_default",
            "collection_name",
            "public_domain",
            "drive_relative_path",
        ]


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class IntakeUploadForm(forms.Form):
    files = forms.FileField(widget=MultipleFileInput, required=True)
