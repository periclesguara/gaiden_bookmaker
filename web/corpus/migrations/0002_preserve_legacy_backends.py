from django.db import migrations


def preserve_backends(apps, schema_editor):
    Project = apps.get_model("writer", "StoryProject")
    Config = apps.get_model("corpus", "ProjectCorpus")
    alias = schema_editor.connection.alias
    for pk in Project.objects.using(alias).values_list("pk", flat=True).iterator():
        Config.objects.using(alias).get_or_create(project_id=pk, defaults={"backend": "LEGACY_WRITER"})


class Migration(migrations.Migration):
    dependencies = [("corpus", "0001_initial")]
    operations = [migrations.RunPython(preserve_backends, migrations.RunPython.noop)]
