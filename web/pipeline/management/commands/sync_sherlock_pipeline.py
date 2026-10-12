from pathlib import Path

from django.core.management.base import BaseCommand

from pipeline.models import PipelineJob


class Command(BaseCommand):
    help = "Sincroniza a pipeline do Sherlock (book_0001) com base nos arquivos em data/"

    def handle(self, *args, **options):
        project_root = Path(__file__).resolve().parents[4]
        data_dir = project_root / "data"

        book_code = "book_0001"
        book_title = "Sherlock Holmes - Book 1"

        normalize_path = data_dir / "normalized" / "book_0001_v2.txt"
        self._upsert_job(
            book_code=book_code,
            book_title=book_title,
            language="en",
            stage="normalize",
            filepath=normalize_path,
            exists=normalize_path.exists(),
        )

        split_dir = data_dir / "chunks" / book_code / "split_01"
        split_exists = split_dir.exists() and any(split_dir.glob("*.txt"))
        self._upsert_job(
            book_code=book_code,
            book_title=book_title,
            language="en",
            stage="split",
            filepath=split_dir,
            exists=split_exists,
        )

        translate_files = {
            "en": data_dir / "chunks" / book_code / "refine_en_01" / "merged_en_modern_2026.txt",
            "es": data_dir / "chunks" / book_code / "refine_es_01" / "merged_es_2025.txt",
            "ptbr": data_dir / "chunks" / book_code / "refine_ptbr_01" / "merged_ptbr_2025.txt",
        }
        for language, path in translate_files.items():
            self._upsert_job(
                book_code=book_code,
                book_title=book_title,
                language=language,
                stage="translate",
                filepath=path,
                exists=path.exists(),
            )

        refine_files = {
            "en": data_dir / "chunks" / book_code / "refine_en_01" / "merged_refined_en_2025.txt",
            "es": data_dir / "chunks" / book_code / "refine_es_01" / "merged_refined_es_2025.txt",
            "ptbr": data_dir / "chunks" / book_code / "refine_ptbr_01" / "merged_refined_ptbr_2025.txt",
        }
        for language, path in refine_files.items():
            self._upsert_job(
                book_code=book_code,
                book_title=book_title,
                language=language,
                stage="refine",
                filepath=path,
                exists=path.exists(),
            )

        polish_en = data_dir / "chunks" / book_code / "refine_en_01" / "merged_polished_en_2025.txt"
        self._upsert_job(
            book_code=book_code,
            book_title=book_title,
            language="en",
            stage="polish",
            filepath=polish_en,
            exists=polish_en.exists(),
        )

        self.stdout.write(self.style.SUCCESS("Sync do Sherlock (book_0001) concluido."))

    def _upsert_job(self, book_code, book_title, language, stage, filepath, exists: bool) -> None:
        status = "SUCCESS" if exists else "PENDING"
        message = "" if exists else "Arquivo ainda nao encontrado"

        job, created = PipelineJob.objects.update_or_create(
            book_code=book_code,
            language=language,
            stage=stage,
            defaults={
                "book_title": book_title,
                "status": status,
                "filepath": str(filepath),
                "message": message,
            },
        )

        action = "Criado" if created else "Atualizado"
        self.stdout.write(f"{action}: {book_code} [{language}] - {stage} -> {status} ({filepath})")
