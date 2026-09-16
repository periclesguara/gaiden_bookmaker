from django import forms

from .models import Chapter, SourceDocument, StoryProject
from .services.google_docs import extract_document_id


class VersionForm(forms.Form):
    content = forms.CharField(widget=forms.Textarea(attrs={"rows": 24}))
    change_note = forms.CharField(max_length=500, required=False)


class PromotionForm(forms.Form):
    editor_approval = forms.CharField(max_length=150)
    reason = forms.CharField(max_length=500)
    confirm = forms.BooleanField(label="I confirm promotion to the official body")


class StoryProjectForm(forms.ModelForm):
    class Meta:
        model = StoryProject
        fields = (
            "title", "language", "premise", "character_bible", "supporting_characters_bible", "antagonist_bible",
            "scenario_bible", "world_bible", "story_direction", "story_outline",
            "chapter_count",
        )
        labels = {
            "title": "Título do projeto",
            "language": "Idioma de escrita",
            "premise": "Premissa",
            "character_bible": "Bíblia do personagem",
            "supporting_characters_bible": "Bíblia dos personagens coadjuvantes",
            "antagonist_bible": "Bíblia do antagonista",
            "scenario_bible": "Cenários e locais",
            "world_bible": "Mundo, época, clima e referências",
            "story_direction": "Direção da história",
            "story_outline": "Roteiro geral",
            "chapter_count": "Quantidade de capítulos",
        }
        widgets = {
            field: forms.Textarea(attrs={"rows": 5})
            for field in (
                "premise", "character_bible", "supporting_characters_bible", "antagonist_bible", "scenario_bible",
                "world_bible", "story_direction", "story_outline",
            )
        }

    def clean_chapter_count(self):
        value = self.cleaned_data["chapter_count"]
        if self.instance.pk:
            highest = self.instance.chapters.order_by("-number").values_list("number", flat=True).first()
            if highest and value < highest:
                raise forms.ValidationError(
                    f"O projeto já possui o Capítulo {highest:02d}; capítulos existentes não são apagados."
                )
        return value


class ProjectSourcesForm(forms.Form):
    sources = forms.ModelMultipleChoiceField(
        queryset=SourceDocument.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Arquivos normalizados do RAG",
    )

    def __init__(self, *args, project: StoryProject, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sources"].queryset = SourceDocument.objects.filter(
            status__in=(SourceDocument.Status.NORMALIZED, SourceDocument.Status.VECTORIZED)
        )
        self.fields["sources"].initial = project.sources.all()


class GoogleDocSourceForm(forms.Form):
    google_doc = forms.CharField(
        label="Google Docs URL ou documentId",
        max_length=500,
        help_text="Apenas leitura; o documento precisa estar compartilhado com a credencial configurada.",
    )

    def clean_google_doc(self):
        value = self.cleaned_data["google_doc"]
        try:
            extract_document_id(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return value


class ChapterForm(forms.ModelForm):
    @property
    def completed_session_numbers(self):
        return list(self.instance.sessions.values_list("number", flat=True)) if self.instance.pk else []

    class Meta:
        model = Chapter
        fields = (
            "title", "direction", "script", "target_words",
            "retrieval_top_k",
        )
        labels = {
            "title": "Título",
            "direction": "Direção geral do capítulo (opcional com cenas)",
            "script": "Roteiro geral do capítulo (opcional com cenas)",
            "target_words": "Meta de palavras",
            "script": "Roteiro completo do capítulo",
            "retrieval_top_k": "Trechos recuperados pelo RAG",
        }
        widgets = {
            "direction": forms.Textarea(attrs={"rows": 5}),
            "script": forms.Textarea(attrs={"rows": 7}),
        }

    def clean(self):
        cleaned = super().clean()
        words = cleaned.get("target_words")
        sessions = 1
        if words and sessions and words < sessions * 400:
            self.add_error(
                "target_words",
                f"Use pelo menos {sessions * 400} palavras para {sessions} sessões.",
            )
        if self.instance.pk and self.instance.generated_text and set(self.changed_data) - {"session_scenes"}:
            raise forms.ValidationError(
                "Parâmetros e roteiro ficam imutáveis após a geração. "
                "Crie um novo capítulo ou uma futura revisão versionada."
            )
        return cleaned
