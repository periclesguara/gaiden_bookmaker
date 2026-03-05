from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0011_alter_bookeditiontemplate_language"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE pipeline_bookeditiontemplate "
                        "ADD COLUMN IF NOT EXISTS editorial_name varchar(120) NOT NULL DEFAULT '';"
                        "ALTER TABLE pipeline_bookeditiontemplate "
                        "ADD COLUMN IF NOT EXISTS edition_year integer NULL;"
                        "ALTER TABLE pipeline_bookeditiontemplate "
                        "ADD COLUMN IF NOT EXISTS edition_copyright_holder varchar(120) NOT NULL DEFAULT '';"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                )
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
