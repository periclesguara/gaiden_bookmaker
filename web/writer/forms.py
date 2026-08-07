from django import forms

from writer.models import Chapter, SourceDocument, StoryProject


class StoryProjectForm(forms.ModelForm):
    class Meta:
        model = StoryProject
        fields = (
            "title", "language", "premise", "character_bible", "antagonist_bible",
            "scenario_bible", "world_bible", "story_direction", "story_outline",
            "chapter_count",
        )
        labels = {
            "title": "Título do projeto",
            "language": "Idioma de escrita",
            "premise": "Premissa",
            "character_bible": "Bíblia do personagem",
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
                "premise", "character_bible", "antagonist_bible", "scenario_bible",
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


class ChapterForm(forms.ModelForm):
    class Meta:
        model = Chapter
        fields = (
            "title", "direction", "script", "target_words", "session_count",
            "retrieval_top_k",
        )
        labels = {
            "title": "Título",
            "direction": "Direção do capítulo",
            "script": "Roteiro do capítulo",
            "target_words": "Meta de palavras",
            "session_count": "Número de sessões (1 a 4)",
            "retrieval_top_k": "Trechos recuperados pelo RAG",
        }
        widgets = {
            "direction": forms.Textarea(attrs={"rows": 5}),
            "script": forms.Textarea(attrs={"rows": 7}),
        }

    def clean(self):
        cleaned = super().clean()
        words = cleaned.get("target_words")
        sessions = cleaned.get("session_count")
        if words and sessions and words < sessions * 400:
            self.add_error(
                "target_words",
                f"Use pelo menos {sessions * 400} palavras para {sessions} sessões.",
            )
        if self.instance.pk and self.instance.sessions.exists() and self.changed_data:
            raise forms.ValidationError(
                "Parâmetros e roteiro ficam imutáveis após a primeira sessão. "
                "Crie um novo capítulo ou uma futura revisão versionada."
            )
        return cleaned
