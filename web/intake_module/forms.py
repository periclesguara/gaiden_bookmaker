from django import forms

from gaiden.application.intake.book_code_allocation import BOOK_CODE_PATTERN

from .models import IntakeBatch, IntakeItem


class IntakeBatchForm(forms.ModelForm):
    class Meta:
        model = IntakeBatch
        fields = [
            "name",
            "author_default",
            "source_language",
            "imprint_default",
            "editor_default",
            "collection_name",
            "public_domain",
        ]


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        defaults = {"accept": ".epub,.txt,.html,.htm"}
        defaults.update(attrs or {})
        super().__init__(defaults)


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        cleaner = super().clean
        if isinstance(data, (list, tuple)):
            return [cleaner(item, initial) for item in data]
        return [cleaner(data, initial)]


class IntakeUploadForm(forms.Form):
    files = MultipleFileField(required=True)


class LocalDirectoryForm(forms.Form):
    local_folder_name = forms.CharField(widget=forms.HiddenInput(), required=False)
    folder_files = MultipleFileField(
        required=False,
        label="Pasta do computador",
        widget=MultipleFileInput(
            attrs={
                "webkitdirectory": True,
                "directory": True,
                "accept": ".epub,.txt,.html,.htm",
            }
        ),
    )


class PrepareCodexForm(forms.Form):
    target_language = forms.SlugField(max_length=20)


class TranslationReturnForm(forms.Form):
    return_file = forms.FileField()


class IntakeItemMetadataForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["book_code"].help_text = (
            "Use book_ seguido de ao menos quatro dígitos. "
            "Após atribuído, o código é imutável."
        )
        if self.instance and self.instance.pk and self.instance.book_code:
            self.fields["book_code"].disabled = True

    def clean_book_code(self):
        existing = self.instance.book_code if self.instance and self.instance.pk else ""
        if existing:
            return existing
        value = (self.cleaned_data.get("book_code") or "").strip()
        if value and not BOOK_CODE_PATTERN.fullmatch(value):
            raise forms.ValidationError(
                "Use o formato book_0033 (mínimo de quatro dígitos)."
            )
        return value

    class Meta:
        model = IntakeItem
        fields = ["confirmed_title", "original_year", "book_code", "target_language"]
