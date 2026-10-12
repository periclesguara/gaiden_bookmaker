from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("writer", "0017_ensure_language_contract_column")]

    operations = [
        migrations.AlterField(
            model_name="sourcevectorindex", name="source",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                       related_name="writer_vector_index", to="editorial.editionbuild"),
        ),
        migrations.AddField(
            model_name="sourcevectorindex", name="intake_work",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                       related_name="writer_vector_index", to="editorial.work"),
        ),
        migrations.AddField(
            model_name="sourcevectorindex", name="normalized_document",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="reference_indexes", to="writer.sourcedocument"),
        ),
    ]
