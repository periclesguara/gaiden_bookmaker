from pathlib import Path

from django.core.management.base import BaseCommand

from gaiden.translate_artifacts import resolve_active_or_latest
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

        chunk_dir = data_dir / "chunks" / book_code / "en"
        chunk_exists = chunk_dir.exists() and any(chunk_dir.glob("*.txt"))
        self._upsert_job(
            book_code=book_code,
            book_title=book_title,
            language="en",
            stage="chunk",
            filepath=chunk_dir,
            exists=chunk_exists,
        )

        translate_files = {
            lang: resolve_active_or_latest(data_dir / "translated" / book_code / lang, book_code, lang)
            for lang in ("en", "es", "ptbr", "de", "fr", "it")
        }
        for language, path in translate_files.items():
            self._upsert_job(
                book_code=book_code,
                book_title=book_title,
                language=language,
                stage="translate",
                filepath=path or (data_dir / "translated" / book_code / language),
                exists=bool(path and path.exists()),
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
