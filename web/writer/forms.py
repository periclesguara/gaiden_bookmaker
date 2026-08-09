from django import forms

from writer.language_contract import language_contract_for, validate_language_contract
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
            "title": forms.TextInput(
                attrs={"placeholder": "Sherlock Holmes — The Devil in Paris"}
            ),
            "language": forms.Select(),
            "premise": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": (
                        "Resuma o conflito central, o mistério e o que está em jogo."
                    ),
                }
            ),
            "character_bible": forms.Textarea(
                attrs={
                    "rows": 8,
                    "placeholder": (
                        "Defina Holmes, Watson e os demais personagens: voz, objetivos, "
                        "limites, relações e fatos de continuidade."
                    ),
                }
            ),
            "antagonist_bible": forms.Textarea(
                attrs={
                    "rows": 8,
                    "placeholder": (
                        "Defina o antagonista: identidade, motivação, método, recursos, "
                        "segredos e relação com o mistério."
                    ),
                }
            ),
            "scenario_bible": forms.Textarea(
                attrs={
                    "rows": 7,
                    "placeholder": (
                        "Liste os cenários de Paris e outros locais relevantes, com a "
                        "função dramática de cada um."
                    ),
                }
            ),
            "world_bible": forms.Textarea(
                attrs={
                    "rows": 7,
                    "placeholder": (
                        "Registre época, clima, costumes, tecnologia e referências "
                        "históricas necessárias à consistência."
                    ),
                }
            ),
            "story_direction": forms.Textarea(
                attrs={
                    "rows": 7,
                    "placeholder": (
                        "Descreva tom, ritmo, ponto de vista, progressão da investigação "
                        "e limites criativos."
                    ),
                }
            ),
            "story_outline": forms.Textarea(
                attrs={
                    "rows": 10,
                    "placeholder": (
                        "Organize os principais acontecimentos, pistas, viradas, "
                        "confronto e resolução."
                    ),
                }
            ),
            "chapter_count": forms.NumberInput(attrs={"min": 1, "max": 100}),
        }
        help_texts = {
            "language": (
                "Selecione EN-US, EN-UK ou PT-BR. O Writer carrega automaticamente "
                "o contrato correspondente antes de enviar o contexto RAG ao Qwen."
            ),
            "chapter_count": (
                "Ao salvar, o Writer cria as linhas que faltam na tabela de capítulos "
                "sem apagar capítulos existentes."
            ),
        }

    def clean(self):
        cleaned = super().clean()
        language = cleaned.get("language")
        if (
            language
            and self.instance.pk
            and "language" in self.changed_data
            and self.instance.chapters.filter(sessions__isnull=False).exists()
        ):
            self.add_error(
                "language",
                "O idioma fica imutável após a primeira sessão. Crie uma revisão versionada.",
            )
        elif language:
            contract = language_contract_for(language)
            validate_language_contract(contract)
            self.instance.language_contract = contract
        return cleaned

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
