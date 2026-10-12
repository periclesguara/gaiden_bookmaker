from django.db import migrations, models


def _ensure_template_columns(apps, schema_editor):
    model = apps.get_model("pipeline", "BookEditionTemplate")
    table_name = model._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(cursor, table_name)
    existing_columns = {getattr(col, "name", col[0]) for col in description}

    if "editorial_name" not in existing_columns:
        field = models.CharField(max_length=120, blank=True, default="")
        field.set_attributes_from_name("editorial_name")
        schema_editor.add_field(model, field)
    if "edition_year" not in existing_columns:
        field = models.IntegerField(blank=True, null=True)
        field.set_attributes_from_name("edition_year")
        schema_editor.add_field(model, field)
    if "edition_copyright_holder" not in existing_columns:
        field = models.CharField(max_length=120, blank=True, default="")
        field.set_attributes_from_name("edition_copyright_holder")
        schema_editor.add_field(model, field)


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0011_alter_bookeditiontemplate_language"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_ensure_template_columns, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="editorial_name",
                    field=models.CharField(blank=True, default="", max_length=120),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="edition_year",
                    field=models.IntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="edition_copyright_holder",
                    field=models.CharField(blank=True, default="", max_length=120),
                ),
            ],
        ),
    ]
