import uuid

from django.db import migrations, models
import django.db.models.deletion


def create_immutable_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        CREATE OR REPLACE FUNCTION pipeline_productionbookmark_prevent_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ProductionBookmark is append-only and immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS pipeline_productionbookmark_immutable
        ON pipeline_productionbookmark;
        CREATE TRIGGER pipeline_productionbookmark_immutable
        BEFORE UPDATE OR DELETE ON pipeline_productionbookmark
        FOR EACH ROW EXECUTE FUNCTION pipeline_productionbookmark_prevent_mutation();
        """
    )


def drop_immutable_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "DROP TRIGGER IF EXISTS pipeline_productionbookmark_immutable ON pipeline_productionbookmark;"
    )
    schema_editor.execute(
        "DROP FUNCTION IF EXISTS pipeline_productionbookmark_prevent_mutation();"
    )


class Migration(migrations.Migration):
    dependencies = [("pipeline", "0019_productionbookmark")]

    operations = [
        migrations.AlterField(
            model_name="productionbookmark",
            name="key",
            field=models.CharField(default=uuid.uuid4, editable=False, max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name="productionbookmark",
            name="edition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="production_bookmarks",
                to="editorial.edition",
            ),
        ),
        migrations.AlterField(
            model_name="productionbookmark",
            name="saved_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.RunPython(create_immutable_trigger, drop_immutable_trigger),
    ]
