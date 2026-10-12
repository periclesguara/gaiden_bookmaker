from django.db import transaction

from ..models import Chapter, ProjectLanguageRule, ProjectReference, StoryProject
from ..languages import LANGUAGE_NAMES
from .manuscripts import ensure_project_writable


@transaction.atomic
def synchronize_chapters(project: StoryProject) -> None:
    existing_numbers = set(project.chapters.values_list("number", flat=True))
    Chapter.objects.bulk_create([
        Chapter(project=project, number=number, title=f"Capítulo {number:02d}")
        for number in range(1, project.chapter_count + 1)
        if number not in existing_numbers
    ])


@transaction.atomic
def save_reference_bible(project: StoryProject, text: str) -> StoryProject:
    current = StoryProject.objects.select_for_update().get(pk=project.pk)
    ensure_project_writable(current)
    current.reference_bible = text
    current.save(update_fields=("reference_bible", "updated_at"))
    return current


@transaction.atomic
def save_project_selections(project, *, languages, references):
    current = StoryProject.objects.select_for_update().get(pk=project.pk)
    primary = current.primary_language or current.effective_primary_language
    if primary not in LANGUAGE_NAMES:
        raise ValueError("Idioma principal não suportado.")
    languages = dict(languages)
    languages.setdefault(primary, "")
    current.language_rules.filter(is_primary=True).update(is_primary=False)
    current.language_rules.exclude(language_code__in=languages).delete()
    for code, rules in languages.items():
        if code not in LANGUAGE_NAMES:
            raise ValueError("Idioma não suportado.")
        ProjectLanguageRule.objects.update_or_create(
            project=current, language_code=code,
            defaults={"language_name": LANGUAGE_NAMES[code], "rules": rules, "is_primary": code == primary},
        )
    selected = {}
    for row in references:
        selected.setdefault(row["work_id"], row)
    for work_id, row in selected.items():
        existing = current.references.filter(intake_work_id=work_id).first()
        if existing is None and row.get("source_id") is not None:
            existing = current.references.filter(source_id=row.get("source_id")).first()
        roles = [row["reference_role"]]
        if existing and (existing.roles or ["OTHER"])[0] == row["reference_role"]:
            roles = existing.roles
        if existing:
            existing.intake_work_id = work_id
            existing.source_id = row.get("source_id")
            existing.roles = roles
            existing.notes = row["notes"]
            existing.save(update_fields=("intake_work", "source", "roles", "notes", "updated_at"))
        else:
            ProjectReference.objects.create(
                project=current,
                intake_work_id=work_id,
                source_id=row.get("source_id"),
                roles=roles,
                notes=row["notes"],
            )
