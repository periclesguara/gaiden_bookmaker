from django import forms
from django.core.exceptions import ValidationError

from gaiden.domain.editorial.collections import COLLECTION_KIND_CHOICES

from .models import Collection, CollectionItem


class CollectionCreateForm(forms.ModelForm):
    item_count = forms.IntegerField(min_value=2, max_value=10)

    class Meta:
        model = Collection
        fields = ["title", "subtitle", "collection_kind", "author_display_name", "language", "item_count"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["collection_kind"].choices = COLLECTION_KIND_CHOICES


class CollectionItemForm(forms.ModelForm):
    order_index = forms.IntegerField(min_value=1, max_value=10)

    class Meta:
        model = CollectionItem
        fields = ["order_index", "author_name", "work_title"]


class CollectionUploadForm(forms.Form):
    item_id = forms.IntegerField(widget=forms.HiddenInput)
    source_file = forms.FileField()

    def clean_source_file(self):
        source_file = self.cleaned_data["source_file"]
        name = (source_file.name or "").lower()
        if not name.endswith(".html") and not name.endswith(".htm"):
            raise ValidationError("Collection accepts only HTML uploads at input stage.")
        return source_file
