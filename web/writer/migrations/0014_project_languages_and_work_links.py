from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


def populate_work_links(apps, schema_editor):
    ProjectReference = apps.get_model("writer", "ProjectReference")
    for reference in ProjectReference.objects.select_related("source__edition__work"):
        work_id = reference.source.edition.work_id
        ProjectReference.objects.filter(pk=reference.pk, intake_work__isnull=True).update(intake_work_id=work_id)


class Migration(migrations.Migration):
    dependencies = [("writer", "0013_reference_system")]
    operations = [
        migrations.AddField(
            model_name="storyproject", name="primary_language",
            field=models.CharField(blank=True, choices=[("en", "English"), ("pt-BR", "Português (Brasil)"), ("fr", "Français"), ("de", "Deutsch"), ("es", "Español"), ("it", "Italiano")], max_length=16),
        ),
        migrations.AddField(
            model_name="projectreference", name="intake_work",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="writer_project_work_references", to="editorial.work"),
        ),
        migrations.CreateModel(
            name="ProjectLanguageRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(choices=[("en", "English"), ("pt-BR", "Português (Brasil)"), ("fr", "Français"), ("de", "Deutsch"), ("es", "Español"), ("it", "Italiano")], max_length=16)),
                ("language_name", models.CharField(max_length=64)), ("rules", models.TextField(blank=True)),
                ("is_primary", models.BooleanField(default=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="language_rules", to="writer.storyproject")),
            ],
            options={"ordering": ("-is_primary", "language_code")},
        ),
        migrations.RunPython(populate_work_links, migrations.RunPython.noop),
        migrations.AddConstraint(model_name="projectreference", constraint=models.UniqueConstraint(condition=Q(("intake_work__isnull", False)), fields=("project", "intake_work"), name="writer_unique_project_intake_work")),
        migrations.AddConstraint(model_name="projectlanguagerule", constraint=models.UniqueConstraint(fields=("project", "language_code"), name="writer_unique_project_language")),
        migrations.AddConstraint(model_name="projectlanguagerule", constraint=models.UniqueConstraint(condition=Q(("is_primary", True)), fields=("project",), name="writer_one_primary_language")),
    ]
