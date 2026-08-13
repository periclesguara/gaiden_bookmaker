import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0023_restore_preserved_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="EditionMetadata",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("edition_code", models.CharField(blank=True, max_length=100, null=True, unique=True)),
                ("commercial_title", models.CharField(blank=True, max_length=255)),
                ("subtitle", models.CharField(blank=True, max_length=255)),
                ("original_title", models.CharField(blank=True, max_length=255)),
                ("author_first_name", models.CharField(blank=True, max_length=120)),
                ("author_last_name", models.CharField(blank=True, max_length=120)),
                ("author_pseudonym", models.CharField(blank=True, max_length=200)),
                ("regional_language", models.CharField(blank=True, choices=[("pt-BR", "Português (Brasil)"), ("en-US", "English (United States)"), ("en-GB", "English (United Kingdom)"), ("fr-FR", "Français (France)"), ("de-DE", "Deutsch (Deutschland)"), ("it-IT", "Italiano (Italia)")], max_length=5)),
                ("original_language", models.CharField(blank=True, max_length=20)),
                ("imprint_name", models.CharField(blank=True, default="RinoBooks", max_length=150)),
                ("collection_name", models.CharField(blank=True, max_length=255)),
                ("edition_number", models.PositiveIntegerField(blank=True, null=True)),
                ("publication_year", models.PositiveIntegerField(blank=True, null=True)),
                ("isbn", models.CharField(blank=True, max_length=32)),
                ("edition_format", models.CharField(blank=True, choices=[("EPUB", "EPUB"), ("PRINT", "Impresso"), ("AUDIOBOOK", "Audiolivro"), ("OTHER", "Outro")], default="EPUB", max_length=20)),
                ("slug", models.SlugField(blank=True, max_length=255, null=True, unique=True)),
                ("seo_title", models.CharField(blank=True, max_length=255)),
                ("seo_description", models.TextField(blank=True)),
                ("description", models.TextField(blank=True)),
                ("short_description", models.TextField(blank=True)),
                ("keywords", models.JSONField(blank=True, default=list)),
                ("primary_category", models.CharField(blank=True, max_length=120)),
                ("subcategory", models.CharField(blank=True, max_length=120)),
                ("theme", models.CharField(blank=True, max_length=180)),
                ("target_audience", models.CharField(blank=True, max_length=180)),
                ("cover_alt", models.CharField(blank=True, max_length=255)),
                ("work_type", models.CharField(blank=True, choices=[("PUBLIC_DOMAIN", "Domínio público"), ("DERIVATIVE", "Obra derivada"), ("ORIGINAL_RINOBOOKS", "Original RinoBooks")], max_length=30)),
                ("base_work_year", models.PositiveIntegerField(blank=True, null=True)),
                ("consulted_source", models.TextField(blank=True)),
                ("legal_basis", models.TextField(blank=True)),
                ("edition_nature", models.CharField(blank=True, max_length=255)),
                ("editorial_modifications", models.TextField(blank=True)),
                ("authorized_territories", models.TextField(blank=True)),
                ("blocked_territories", models.TextField(blank=True)),
                ("rights_evidence", models.TextField(blank=True)),
                ("price", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("currency", models.CharField(blank=True, choices=[("BRL", "BRL"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")], max_length=3)),
                ("expected_release_date", models.DateField(blank=True, null=True)),
                ("hotmart_url", models.URLField(blank=True)),
                ("lulu_url", models.URLField(blank=True)),
                ("sample_title", models.CharField(blank=True, max_length=255)),
                ("sample_content", models.TextField(blank=True)),
                ("promotional_images", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("DRAFT", "Rascunho"), ("READY", "Validado para exportação")], default="DRAFT", max_length=10)),
                ("validated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("edition", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="metadata", to="editorial.edition")),
            ],
            options={
                "db_table": "edition_metadata",
                "ordering": ["edition__work__code", "regional_language"],
            },
        ),
    ]
