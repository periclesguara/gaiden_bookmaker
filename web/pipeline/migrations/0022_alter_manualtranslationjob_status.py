from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0021_translationjobevent_translationunit_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="manualtranslationjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("EXPORTED", "Aguardando retorno"),
                    ("IMPORTED", "Tradução importada"),
                    ("FAILED", "Falha recuperável"),
                    ("SPLIT_PENDING", "Split pendente"),
                    ("SPLITTING", "Separando capítulos"),
                    ("SPLIT_REVIEW_REQUIRED", "Split requer revisão"),
                    ("SPLIT_VALIDATED", "Split validado"),
                    ("DRIVE_EXPORTING", "Exportando ao Drive"),
                    ("DRIVE_READY", "Drive pronto"),
                    ("TRANSLATION_IN_PROGRESS", "Tradução em andamento"),
                    ("PARTIAL_RETURN", "Retorno parcial"),
                    ("RETURNS_READY", "Retornos prontos"),
                    ("VALIDATING_RETURNS", "Validando retornos"),
                    ("MERGE_READY", "Merge liberado"),
                    ("MERGING", "Executando merge"),
                    ("MERGED", "Merge concluído"),
                    ("VALIDATED", "Manuscrito validado"),
                    ("COMPLETED", "Tradução pronta"),
                    ("BLOCK_01_COMPLETE", "Bloco 01 concluído"),
                    ("FAILED_RETRYABLE", "Falha recuperável v2"),
                    ("CONFLICT", "Conflito"),
                    ("REJECTED", "Rejeitado"),
                ],
                default="EXPORTED",
                max_length=32,
            ),
        ),
    ]
