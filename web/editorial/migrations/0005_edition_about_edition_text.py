from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0004_alter_edition_country"),
    ]

    # NOTE:
    # `about_edition_text` is already introduced in 0003_edition_frontmatter_fields.
    # Keep 0005 as no-op to avoid duplicate-column failures on fresh databases.
    operations = []
