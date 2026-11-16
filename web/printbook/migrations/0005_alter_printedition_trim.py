from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printbook", "0004_editorial_workflow")]

    operations = [
        migrations.AlterField(
            model_name="printedition",
            name="trim",
            field=models.CharField(
                choices=[
                    ("4x6", "4 × 6 in · IngramSpark"), ("4.25x7", "4,25 × 7 in · IngramSpark"),
                    ("4.37x7", "4,37 × 7 in · IngramSpark"), ("4.72x7.48", "4,72 × 7,48 in · IngramSpark"),
                    ("5x7", "5 × 7 in · IngramSpark"), ("5x8", "5 × 8 in · Amazon / IngramSpark"),
                    ("5.06x7.81", "5,06 × 7,81 in · Amazon / IngramSpark"),
                    ("5.25x8", "5,25 × 8 in · Amazon / IngramSpark"), ("5.5x8", "5,5 × 8 in · IngramSpark"),
                    ("5.5x8.5", "5,5 × 8,5 in · Amazon / IngramSpark"),
                    ("5.83x8.27", "5,83 × 8,27 in (A5) · IngramSpark"), ("6x8", "6 × 8 in · IngramSpark"),
                    ("6x9", "6 × 9 in · Amazon / IngramSpark"),
                    ("6.14x9.21", "6,14 × 9,21 in · Amazon / IngramSpark"),
                    ("6.14x9.252", "6,14 × 9,252 in · IngramSpark"),
                    ("6.69x9.61", "6,69 × 9,61 in · Amazon / IngramSpark"), ("7x9", "7 × 9 in · IngramSpark"),
                    ("7x10", "7 × 10 in · Amazon / IngramSpark"),
                    ("7.44x9.69", "7,44 × 9,69 in · Amazon / IngramSpark"),
                    ("7.5x9.25", "7,5 × 9,25 in · Amazon / IngramSpark"), ("8x8", "8 × 8 in · IngramSpark"),
                    ("8x10", "8 × 10 in · Amazon / IngramSpark"),
                    ("8.25x6", "8,25 × 6 in · Amazon / IngramSpark"),
                    ("8.25x8.25", "8,25 × 8,25 in · Amazon / IngramSpark"),
                    ("8.25x11", "8,25 × 11 in · Amazon / IngramSpark"),
                    ("8.27x11.69", "8,27 × 11,69 in (A4) · Amazon"),
                    ("8.5x8.5", "8,5 × 8,5 in · Amazon / IngramSpark"),
                    ("8.5x11", "8,5 × 11 in · Amazon / IngramSpark"),
                ],
                default="6x9", max_length=20,
            ),
        ),
    ]
