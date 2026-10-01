from django import forms

from editorial.models import EditionBuild
from .models import Chapter, ProjectReference, SourceDocument, StoryProject
from .services.google_docs import extract_document_id


class IntakeSourceChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, source):
        try:
            vector_status = source.writer_vector_index.get_status_display()
        except Exception:
            vector_status = "Não vetorizado"
        title = source.edition.title or source.edition.work.title
        author = source.edition.author or source.edition.work.author.name
        return (
            f"{title} · {author} · {source.language_code} · Genre: — · Category: — · "
            f"Source: {source.status} · Vectorization: {vector_status}"
        )


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
            "scenario_bible", "world_bible", "reference_bible", "story_direction", "story_outline",
            "drive_project_folder_id",
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
            "reference_bible": "Reference Bible",
            "story_direction": "Direção da história",
            "story_outline": "Roteiro geral",
            "drive_project_folder_id": "Pasta do projeto no Google Drive",
            "chapter_count": "Quantidade de capítulos",
        }
        widgets = {
            field: forms.Textarea(attrs={"rows": 5})
            for field in (
                "premise", "character_bible", "supporting_characters_bible", "antagonist_bible", "scenario_bible",
                "world_bible", "reference_bible", "story_direction", "story_outline",
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


class ProjectReferenceSelectionForm(forms.Form):
    sources = IntakeSourceChoiceField(
        queryset=EditionBuild.objects.none(), widget=forms.CheckboxSelectMultiple,
        label="Select works from Intake", required=False,
    )

    def __init__(self, *args, project: StoryProject, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sources"].queryset = EditionBuild.objects.filter(
            status=EditionBuild.STATUS_DONE, is_final=True, validation_passed=True
        ).select_related("edition__work__author", "edition__language", "writer_vector_index")
        self.fields["sources"].initial = project.references.values_list("source_id", flat=True)


class ProjectReferenceForm(forms.ModelForm):
    roles_text = forms.CharField(label="Reference Role", required=False)

    class Meta:
        model = ProjectReference
        fields = ("enabled", "notes")
        labels = {"enabled": "Enabled", "notes": "Reference notes"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles_text"].initial = ", ".join(self.instance.roles or [])

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.roles = list(dict.fromkeys(
            value.strip().upper().replace(" ", "_")
            for value in self.cleaned_data.get("roles_text", "").split(",") if value.strip()
        ))
        if commit:
            instance.save()
        return instance


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


class ManuscriptUploadForm(forms.Form):
    manuscript = forms.FileField(label="BROWSE COMPUTER", widget=forms.ClearableFileInput(
        attrs={"accept": ".txt,.md,.docx", "data-final-upload": "1"}))

    def clean_manuscript(self):
        uploaded = self.cleaned_data["manuscript"]
        suffix = uploaded.name.rsplit(".", 1)[-1].casefold() if "." in uploaded.name else ""
        if suffix not in {"md", "txt", "docx"}:
            raise forms.ValidationError("Use um arquivo .md, .txt ou .docx.")
        if not uploaded.size:
            raise forms.ValidationError("O manuscrito não pode estar vazio.")
        if uploaded.size > 50 * 1024 * 1024:
            raise forms.ValidationError("O manuscrito excede o limite de 50 MiB.")
        return uploaded


class WriterIntakeForm(forms.Form):
    author_name = forms.CharField(label="Autor / Contributor", max_length=200)
    publisher = forms.CharField(label="Publisher / Imprint", max_length=255, initial="RinoBooks")


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
