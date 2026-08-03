from django import forms


class VersionForm(forms.Form):
    content = forms.CharField(widget=forms.Textarea(attrs={"rows": 24}))
    change_note = forms.CharField(max_length=500, required=False)


class PromotionForm(forms.Form):
    editor_approval = forms.CharField(max_length=150)
    confirm = forms.BooleanField(label="I confirm promotion to the official body")
