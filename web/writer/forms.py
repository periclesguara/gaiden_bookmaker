from django import forms

from editorial.models import EditionBuild, Work
from .models import Chapter, ProjectLanguageRule, ProjectReference, SourceDocument, StoryProject
from .languages import LANGUAGE_CHOICES
from .reference_bible import DEFAULT_REFERENCE_BIBLE
from .services.reference_catalog import REFERENCE_ROLES, serialize_work, work_queryset
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


class ReferenceBibleForm(forms.ModelForm):
    reference_bible = forms.CharField(
        label="Bíblia de Referências",
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 18}),
    )

    class Meta:
        model = StoryProject
        fields = ("reference_bible",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and self.instance and not self.instance.reference_bible:
            self.initial["reference_bible"] = DEFAULT_REFERENCE_BIBLE


class CorpusSelectionForm(forms.Form):
    work_ids = forms.ModelMultipleChoiceField(queryset=Work.objects.all(), required=False)
    remove_work_ids = forms.ModelMultipleChoiceField(queryset=Work.objects.all(), required=False)

    def clean(self):
        cleaned = super().clean()
        if len(cleaned.get("work_ids", [])) > 500:
            raise forms.ValidationError("Selecione no máximo 500 obras.")
        return cleaned


class StoryProjectForm(forms.ModelForm):
    language = forms.ChoiceField(label="Idioma principal de escrita", choices=LANGUAGE_CHOICES)
    project_languages = forms.MultipleChoiceField(
        label="Idiomas do projeto", choices=LANGUAGE_CHOICES, required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    work_references = forms.JSONField(required=False, widget=forms.HiddenInput)
    corpus_removed_work_ids = forms.JSONField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = StoryProject
        fields = (
            "title", "language", "premise", "character_bible", "supporting_characters_bible", "antagonist_bible",
            "scenario_bible", "world_bible", "story_direction", "story_outline",
            "chapter_count",
        )
        labels = {
            "title": "Título do projeto",
            "language": "Idioma principal de escrita",
            "premise": "Premissa",
            "character_bible": "Bíblia do personagem",
            "supporting_characters_bible": "Bíblia dos personagens coadjuvantes",
            "antagonist_bible": "Bíblia do antagonista",
            "scenario_bible": "Cenários e locais",
            "world_bible": "Mundo, época, clima e referências",
            "reference_bible": "Bíblia de Referências",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        primary = self.instance.effective_primary_language if self.instance.pk else "en"
        rules = {rule.language_code: rule for rule in self.instance.language_rules.all()} if self.instance.pk else {}
        self.initial["language"] = primary
        self.initial["project_languages"] = list(dict.fromkeys([primary, *rules]))
        for code, name in LANGUAGE_CHOICES:
            self.fields[f"rules_{code}"] = forms.CharField(
                label=f"Regras do idioma — {name}", required=False, strip=False,
                widget=forms.Textarea(attrs={"rows": 6}),
                initial=rules[code].rules if code in rules else "",
            )
        if self.instance.pk:
            self.initial["work_references"] = [
                {"source_id": ref.source_id, "work_id": ref.intake_work_id or ref.source.edition.work_id,
                 "reference_role": (ref.roles or ["OTHER"])[0], "notes": ref.notes}
                for ref in self.instance.references.select_related("source__edition")
            ]

    @property
    def reference_rows(self):
        if not self.is_bound:
            return self.initial.get("work_references") or []
        try:
            rows = self.fields["work_references"].clean(self.data.get("work_references"))
        except forms.ValidationError:
            return []
        return rows if isinstance(rows, list) else []

    @property
    def language_fields(self):
        return [(code, name, self[f"rules_{code}"]) for code, name in LANGUAGE_CHOICES]

    def clean_work_references(self):
        rows = self.cleaned_data.get("work_references") or []
        if not isinstance(rows, list) or len(rows) > 500:
            raise forms.ValidationError("Selecione no máximo 500 obras.")
        selected = {}
        for row in rows:
            if not isinstance(row, dict) or type(row.get("work_id")) is not int:
                raise forms.ValidationError("Vínculo de obra inválido.")
            work = work_queryset().filter(pk=row["work_id"]).first()
            if work is None:
                raise forms.ValidationError("A obra selecionada não está disponível no Intake.")
            source_id = serialize_work(work)["source_id"]
            role = row.get("reference_role", "OTHER")
            notes = row.get("notes", "")
            if role not in REFERENCE_ROLES or not isinstance(notes, str) or len(notes) > 20000:
                raise forms.ValidationError("Função ou observação inválida.")
            selected.setdefault(work.pk, {"source_id": source_id, "work_id": work.pk, "reference_role": role, "notes": notes})
        return list(selected.values())

    def clean_corpus_removed_work_ids(self):
        ids = self.cleaned_data.get("corpus_removed_work_ids") or []
        if not isinstance(ids, list) or len(ids) > 500 or any(type(value) is not int for value in ids):
            raise forms.ValidationError("Remoção de vínculo inválida.")
        return ids

    def save(self, commit=True):
        project = super().save(commit=False)
        project.primary_language = self.cleaned_data["language"]
        if commit:
            from django.db import transaction
            with transaction.atomic():
                project.save()
                self.save_selections(project)
        return project

    def save_selections(self, project):
        from .services.projects import save_project_selections
        languages = list(dict.fromkeys([self.cleaned_data["language"], *self.cleaned_data["project_languages"]]))
        save_project_selections(project, languages={code: self.cleaned_data[f"rules_{code}"] for code in languages}, references=self.cleaned_data["work_references"])
        from .services.corpus import save_corpus_selection
        retained = {row["work_id"] for row in self.cleaned_data["work_references"]}
        removed = set(self.cleaned_data["corpus_removed_work_ids"]) - retained
        if removed:
            save_corpus_selection(project, [], Work.objects.filter(pk__in=removed))

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
