from django.db import migrations, models


def _ensure_template_columns(apps, schema_editor):
    model = apps.get_model("pipeline", "BookEditionTemplate")
    table_name = model._meta.db_table

    def _column_names():
        with schema_editor.connection.cursor() as cursor:
            description = schema_editor.connection.introspection.get_table_description(cursor, table_name)
        return {getattr(col, "name", col[0]) for col in description}

    existing_columns = _column_names()
    fields = {
        "original_publication_date": models.DateField(blank=True, null=True),
        "original_author_death_date": models.DateField(blank=True, null=True),
        "work_kind": models.CharField(
            choices=[("AUTHORIAL", "Obra autoral"), ("PUBLIC_DOMAIN", "Obra de dominio publico")],
            default="AUTHORIAL",
            max_length=20,
        ),
        "registration_status": models.CharField(
            choices=[
                ("DRAFT", "Draft"),
                ("REGISTERED", "Registered"),
                ("READY_FOR_BLOCK_02", "Ready for Block 02"),
            ],
            default="DRAFT",
            max_length=30,
        ),
        "source_file_type": models.CharField(blank=True, default="", max_length=10),
        "source_original_name": models.CharField(blank=True, default="", max_length=255),
        "source_saved_path": models.CharField(blank=True, default="", max_length=500),
        "source_file_size": models.BigIntegerField(blank=True, null=True),
        "source_uploaded_at": models.DateTimeField(blank=True, null=True),
        "source_file_sha256": models.CharField(blank=True, default="", max_length=64),
        "source_uploaded_by": models.CharField(blank=True, default="", max_length=150),
    }

    for name, field in fields.items():
        if name in existing_columns:
            continue
        field.set_attributes_from_name(name)
        schema_editor.add_field(model, field)


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0013_ensure_bookeditiontemplate_columns"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_ensure_template_columns, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="original_author_death_date",
                    field=models.DateField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="original_publication_date",
                    field=models.DateField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="registration_status",
                    field=models.CharField(
                        choices=[
                            ("DRAFT", "Draft"),
                            ("REGISTERED", "Registered"),
                            ("READY_FOR_BLOCK_02", "Ready for Block 02"),
                        ],
                        default="DRAFT",
                        max_length=30,
                    ),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="source_file_sha256",
                    field=models.CharField(blank=True, default="", max_length=64),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="source_file_size",
                    field=models.BigIntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="source_file_type",
                    field=models.CharField(blank=True, default="", max_length=10),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="source_original_name",
                    field=models.CharField(blank=True, default="", max_length=255),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="source_saved_path",
                    field=models.CharField(blank=True, default="", max_length=500),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="source_uploaded_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="source_uploaded_by",
                    field=models.CharField(blank=True, default="", max_length=150),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="work_kind",
                    field=models.CharField(
                        choices=[("AUTHORIAL", "Obra autoral"), ("PUBLIC_DOMAIN", "Obra de dominio publico")],
                        default="AUTHORIAL",
                        max_length=20,
                    ),
                ),
            ],
        ),
    ]
