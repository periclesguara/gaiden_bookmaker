from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("writer", "0015_state_language_contract")]
    operations = [
        migrations.AlterField(
            model_name="projectreference",
            name="source",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="writer_project_references", to="editorial.editionbuild"),
        ),
    ]
