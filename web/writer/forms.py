from django import forms

from writer.language_contract import language_contract_for, validate_language_contract
from writer.models import Chapter, SourceDocument, StoryProject


class StoryProjectForm(forms.ModelForm):
    class Meta:
        model = StoryProject
        fields = (
            "title", "writing_mode", "language", "premise", "character_bible", "antagonist_bible",
            "supporting_characters_bible", "scenario_bible", "world_bible",
            "story_direction", "story_outline",
            "chapter_count",
        )
        labels = {
            "title": "Título do projeto",
            "writing_mode": "Modo de escrita",
            "language": "Idioma de criação",
            "premise": "Premissa",
            "character_bible": "Bíblia do personagem",
            "antagonist_bible": "Bíblia do antagonista",
            "supporting_characters_bible": "Bíblia dos coadjuvantes",
            "scenario_bible": "Cenários e locais",
            "world_bible": "Mundo, época, clima e referências",
            "story_direction": "Direção da história",
            "story_outline": "Roteiro geral",
            "chapter_count": "Quantidade de capítulos",
        }
        widgets = {
            field: forms.Textarea(attrs={"rows": 5})
            for field in (
                "premise", "character_bible", "antagonist_bible",
                "supporting_characters_bible", "scenario_bible", "world_bible",
                "story_direction", "story_outline",
            )
        }
        widgets["writing_mode"] = forms.Select()
        widgets["language"] = forms.Select()
        help_texts = {
            "writing_mode": (
                "Fiction usa bíblias criativas. Nonfiction desenvolve o texto-base de cada "
                "capítulo com direção e fontes recuperadas pelo RAG."
            ),
            "language": (
                "Selecione EN-US, EN-UK ou PT-BR. O Writer carrega automaticamente "
                "o contrato correspondente antes de enviar o contexto RAG ao Qwen."
            ),
        }

    def clean(self):
        cleaned = super().clean()
        language = cleaned.get("language")
        writing_mode = cleaned.get("writing_mode")
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
            if writing_mode == StoryProject.WritingMode.NONFICTION:
                contract["source_language"] = language
                contract["operation"] = "original"
            validate_language_contract(contract)
            self.instance.language_contract = contract
        if (
            self.instance.pk
            and "writing_mode" in self.changed_data
            and self.instance.chapters.filter(sessions__isnull=False).exists()
        ):
            self.add_error(
                "writing_mode",
                "O modo de escrita fica imutável após a primeira sessão.",
            )
        if (
            self.instance.pk
            and "supporting_characters_bible" in self.changed_data
            and self.instance.chapters.filter(sessions__isnull=False).exists()
        ):
            self.add_error(
                "supporting_characters_bible",
                "Após a primeira sessão, use a atualização versionada dos coadjuvantes.",
            )
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


class SupportingCastUpdateForm(forms.Form):
    instruction = forms.CharField(
        min_length=10,
        max_length=6000,
        label="Atualização ou gap de continuidade",
        help_text=(
            "Descreva o novo personagem, a inconsistência a corrigir e, se houver, "
            "a obra, o capítulo e o personagem usados como referência semântica."
        ),
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": (
                    "Ex.: Adicionar Mycroft ao capítulo 6. Consultar as aparições "
                    "canônicas no RAG, registrar os traços aproveitados e preservar "
                    "as diferenças desta versão."
                ),
            }
        ),
    )


class ChapterForm(forms.ModelForm):
    class Meta:
        model = Chapter
        fields = (
            "title", "direction", "script", "source_guidance", "target_words",
            "session_count", "retrieval_top_k",
        )
        labels = {
            "title": "Título",
            "direction": "Direção do capítulo",
            "script": "Roteiro do capítulo",
            "source_guidance": "Referências e consultas para o RAG",
            "target_words": "Meta de palavras",
            "session_count": "Número de sessões (1 a 4)",
            "retrieval_top_k": "Trechos recuperados pelo RAG",
        }
        widgets = {
            "direction": forms.Textarea(attrs={"rows": 5}),
            "script": forms.Textarea(attrs={"rows": 7}),
            "source_guidance": forms.Textarea(attrs={"rows": 5}),
        }
        help_texts = {
            "source_guidance": (
                "Indique obras, autores, documentos, assuntos, períodos ou perguntas que "
                "devem orientar a recuperação das fontes deste capítulo."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if (
            self.instance.project_id
            and self.instance.project.writing_mode == StoryProject.WritingMode.NONFICTION
        ):
            self.fields["script"].label = "Texto-base, argumentos e notas a desenvolver"
            self.fields["script"].help_text = (
                "O Qwen deve melhorar, ampliar e organizar este material sem substituir a tese."
            )
            self.fields["direction"].help_text = (
                "Defina a tese, o objetivo, os limites e a estrutura esperada do capítulo."
            )

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
