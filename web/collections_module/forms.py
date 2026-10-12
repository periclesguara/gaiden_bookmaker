from django import forms
from django.forms import BaseFormSet, formset_factory
from django.core.exceptions import ValidationError

from gaiden.domain.editorial.collections import COLLECTION_KIND_CHOICES

from .models import Collection, CollectionItem


LANGUAGE_CHOICES = [
    ("en", "English"),
    ("ptbr", "Portugues (Brasil)"),
    ("es", "Espanol"),
    ("de", "Deutsch"),
    ("fr", "Francais"),
    ("it", "Italiano"),
]


class CollectionCreateForm(forms.ModelForm):
    item_count = forms.IntegerField(
        min_value=2,
        max_value=10,
        widget=forms.Select(choices=[(value, f"{value} itens") for value in range(2, 11)]),
    )

    class Meta:
        model = Collection
        fields = ["title", "subtitle", "collection_kind", "author_display_name", "language", "item_count"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["collection_kind"].choices = COLLECTION_KIND_CHOICES
        self.fields["language"].widget = forms.Select(choices=LANGUAGE_CHOICES)
        self.fields["title"].widget.attrs.update({"placeholder": "Ex.: Sherlock Holmes Essentials"})
        self.fields["subtitle"].widget.attrs.update({"placeholder": "Ex.: Volume 1"})
        self.fields["author_display_name"].widget.attrs.update({"placeholder": "Ex.: Arthur Conan Doyle"})


class CollectionItemForm(forms.ModelForm):
    order_index = forms.IntegerField(min_value=1, max_value=10)

    class Meta:
        model = CollectionItem
        fields = ["order_index", "author_name", "work_title"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["order_index"].widget.attrs.update({"readonly": "readonly"})
        self.fields["author_name"].widget.attrs.update({"placeholder": "Autor do conto ou obra"})
        self.fields["work_title"].widget.attrs.update({"placeholder": "Titulo da obra"})


class BaseCollectionItemBatchFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen_orders: set[int] = set()
        seen_keys: set[tuple[str, str]] = set()
        expected_orders = list(range(1, len(self.forms) + 1))
        submitted_orders: list[int] = []
        for form in self.forms:
            data = getattr(form, "cleaned_data", None) or {}
            if not data:
                continue
            order_index = data.get("order_index")
            author_name = (data.get("author_name") or "").strip()
            work_title = (data.get("work_title") or "").strip()
            if order_index in seen_orders:
                raise ValidationError("Os itens precisam ter uma ordem sequencial sem repeticao.")
            seen_orders.add(order_index)
            submitted_orders.append(order_index)
            duplicate_key = (author_name.lower(), work_title.lower())
            if duplicate_key in seen_keys:
                raise ValidationError("Nao e permitido cadastrar itens duplicados na mesma collection.")
            seen_keys.add(duplicate_key)
        if submitted_orders != expected_orders:
            raise ValidationError("Preencha todos os itens na ordem exibida.")


CollectionItemBatchFormSet = formset_factory(
    CollectionItemForm,
    formset=BaseCollectionItemBatchFormSet,
    extra=0,
)


def build_collection_item_formset(*, item_count: int, data=None, initial=None):
    initial = initial or [{"order_index": index} for index in range(1, item_count + 1)]
    return CollectionItemBatchFormSet(data=data, initial=initial)


class CollectionUploadForm(forms.Form):
    SOURCE_FORMAT_CHOICES = [
        ("txt", "TXT"),
        ("html", "HTML"),
        ("epub", "EPUB"),
    ]

    item_id = forms.IntegerField(widget=forms.HiddenInput)
    source_format = forms.ChoiceField(choices=SOURCE_FORMAT_CHOICES)
    source_file = forms.FileField()

    def clean_source_file(self):
        source_file = self.cleaned_data["source_file"]
        name = (source_file.name or "").lower()
        if not name.endswith((".txt", ".html", ".htm", ".epub")):
            raise ValidationError("Collection accepts only TXT, HTML, HTM or EPUB uploads at input stage.")
        return source_file

    def clean(self):
        cleaned_data = super().clean()
        source_format = cleaned_data.get("source_format")
        source_file = cleaned_data.get("source_file")
        if not source_format or not source_file:
            return cleaned_data
        name = (source_file.name or "").lower()
        allowed = {
            "txt": (".txt",),
            "html": (".html", ".htm"),
            "epub": (".epub",),
        }[source_format]
        if not name.endswith(allowed):
            expected = ", ".join(allowed)
            raise ValidationError(f"Arquivo incompativel com {source_format.upper()}. Envie: {expected}.")
        return cleaned_data
