from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0014_editionpipeline_refine_profile"),
    ]

    operations = [
        migrations.AlterField(
            model_name="edition",
            name="copyright_template",
            field=models.TextField(
                default="Title: {title}\n\nSubtitle: {subtitle}\n\nAuthor: {author}\n\nAdapter: {adapter}\n\nEditor: {editor}\n\nPublication Year: {year}\n\nThe original work, *{title}* by {author},\nis in the public domain worldwide.\n\nCopyright © {year} RinoBooks.\n\nThis modern edition, including translation, adaptation,\nand editorial material, is copyrighted by RinoBooks.\n\nThis edition of *{title}*\nwas produced under the {imprint} imprint.\n\n{imprint} is a registered trademark of RinoBooks.\n\nPublisher:\n{imprint}\n\nAll rights reserved.\n\n{city}, {country} — {year}\n"
            ),
        ),
    ]
