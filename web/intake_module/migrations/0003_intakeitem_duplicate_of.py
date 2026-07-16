from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("intake_module", "0002_intakeitem_pipeline_handoff")]

    operations = [
        migrations.AddField(
            model_name="intakeitem",
            name="duplicate_of",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="duplicate_items",
                to="intake_module.intakeitem",
            ),
        ),
    ]
