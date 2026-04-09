from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Collection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(unique=True)),
                ("pipeline_book_code", models.SlugField(blank=True, default="")),
                ("title", models.CharField(max_length=255)),
                ("subtitle", models.CharField(blank=True, max_length=255)),
                ("collection_kind", models.CharField(choices=[("novel_trilogy", "Novel trilogy"), ("complete_novels", "Complete novels"), ("collected_tales", "Collected tales"), ("selected_stories", "Selected stories"), ("omnibus", "Omnibus"), ("anthology", "Anthology"), ("mixed_collection", "Mixed collection")], max_length=50)),
                ("author_display_name", models.CharField(max_length=255)),
                ("language", models.CharField(max_length=10)),
                ("status", models.CharField(choices=[("COLLECTION_CREATED", "Created"), ("COLLECTION_ITEMS_REGISTERED", "Items registered"), ("COLLECTION_UPLOADS_RECEIVED", "Uploads received"), ("COLLECTION_PREPARED", "Prepared"), ("COLLECTION_NORMALIZED", "Normalized"), ("COLLECTION_MERGED", "Merged"), ("COLLECTION_READY_FOR_PIPELINE", "Ready for pipeline"), ("COLLECTION_PIPELINE_RUNNING", "Pipeline running"), ("COLLECTION_DONE", "Done"), ("COLLECTION_FAILED", "Failed")], max_length=50)),
                ("item_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-id"]},
        ),
        migrations.CreateModel(
            name="CollectionArtifact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("artifact_type", models.CharField(max_length=50)),
                ("language", models.CharField(max_length=10)),
                ("path", models.CharField(max_length=500)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="artifacts", to="collections_module.collection")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="CollectionItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order_index", models.PositiveIntegerField()),
                ("author_name", models.CharField(max_length=255)),
                ("work_title", models.CharField(max_length=255)),
                ("source_filename", models.CharField(blank=True, max_length=255)),
                ("source_original_path", models.CharField(blank=True, max_length=500)),
                ("uploaded_at", models.DateTimeField(blank=True, null=True)),
                ("prep_status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("failed", "Failed"), ("completed", "Completed")], default="pending", max_length=20)),
                ("normalize_status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("failed", "Failed"), ("completed", "Completed")], default="pending", max_length=20)),
                ("merge_status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("failed", "Failed"), ("completed", "Completed")], default="pending", max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="collections_module.collection")),
            ],
            options={"ordering": ["order_index", "id"], "unique_together": {("collection", "order_index"), ("collection", "author_name", "work_title")}},
        ),
        migrations.CreateModel(
            name="CollectionRunState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("current_step", models.CharField(blank=True, default="", max_length=50)),
                ("last_error", models.TextField(blank=True)),
                ("is_locked", models.BooleanField(default=False)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("collection", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="run_state", to="collections_module.collection")),
            ],
        ),
    ]
