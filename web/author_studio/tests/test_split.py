import hashlib
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gaiden.application.author_studio.create_author import create_author
from gaiden.application.author_studio.ingest_work_source import ingest_new_work
from gaiden.application.author_studio.split_work import (
    CHUNKER_VERSION,
    CanonicalChangedDuringSplit,
    SplitConfiguration,
    _build_chunks,
    split_work,
)
from gaiden.application.author_studio.tokenization import DEFAULT_TOKENIZER_NAME, count_tokens
from gaiden.domain.author_studio.enums import SplitOutcome, SplitRunStatus, SplitStatus

from ..models import WorkChunk, WorkSplit, WorkSplitRun

MEDIA_ROOT = tempfile.mkdtemp(prefix="gaiden-author-studio-split-tests-")


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class WorkSplitTests(TestCase):
    def setUp(self):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        Path(MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
        author = create_author("Arthur Conan Doyle")
        paragraphs = [
            f"Paragraph {number}. " + "Sherlock Holmes examined the evidence with careful attention. " * 55
            for number in range(1, 9)
        ]
        upload = SimpleUploadedFile(
            "hound.txt",
            ("THE HOUND\n\nCHAPTER 1\n\n" + "\n\n".join(paragraphs)).encode(),
            content_type="text/plain",
        )
        self.work = ingest_new_work(
            author=author,
            title="The Hound of the Baskervilles",
            original_language="en",
            upload=upload,
        ).work

    def _chunks(self):
        return list(WorkChunk.objects.filter(work=self.work).order_by("sequence"))

    def _snapshot(self):
        return [
            (chunk.pk, chunk.code, chunk.sha256, chunk.text_file.name)
            for chunk in self._chunks()
        ]

    def _replace_canonical(self, suffix="Additional evidence closed the chapter."):
        canonical = self.work.canonical_text
        with canonical.text_file.storage.open(canonical.text_file.name, "rb") as handle:
            payload = handle.read().rstrip() + f"\n\n{suffix}\n".encode()
        with canonical.text_file.storage.open(canonical.text_file.name, "wb") as handle:
            handle.write(payload)
        canonical.sha256 = hashlib.sha256(payload).hexdigest()
        canonical.character_count = len(payload.decode())
        canonical.word_count = len(payload.decode().split())
        canonical.save(update_fields=["sha256", "character_count", "word_count", "updated_at"])
        return canonical

    def test_split_persists_real_tokens_hashes_lines_and_run_created(self):
        result = split_work(work=self.work)
        run = WorkSplit.objects.get(work=self.work)
        history = WorkSplitRun.objects.get(work=self.work)
        self.assertEqual(result.outcome, SplitOutcome.CREATED.value)
        self.assertEqual(run.status, SplitStatus.COMPLETED.value)
        self.assertEqual(run.chunk_count, result.chunk_count)
        self.assertEqual(run.tokenizer_name, DEFAULT_TOKENIZER_NAME)
        self.assertEqual(history.status, SplitRunStatus.COMPLETED.value)
        self.assertEqual(history.outcome, SplitOutcome.CREATED.value)
        for chunk in self._chunks():
            self.assertEqual(chunk.token_count, count_tokens(chunk.text_file.read().decode(), "EN").count)
            self.assertLessEqual(chunk.token_count, 900)
            self.assertLessEqual(chunk.start_line, chunk.end_line)
            with chunk.text_file.storage.open(chunk.text_file.name, "rb") as handle:
                self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), chunk.sha256)

    def test_en_and_en_lowercase_produce_identical_chunks(self):
        canonical = self.work.canonical_text
        with canonical.text_file.open("r") as handle:
            text = handle.read()
        configuration = SplitConfiguration()
        upper = _build_chunks(text, " EN ", configuration)
        lower = _build_chunks(text, "en", configuration)
        self.assertEqual([chunk.token_count for chunk in upper], [chunk.token_count for chunk in lower])
        self.assertEqual([chunk.sha256 for chunk in upper], [chunk.sha256 for chunk in lower])

    def test_maximum_is_rigid_even_for_one_huge_sentence(self):
        text = "FULL TEXT\n\n" + ("elementary " * 5000) + "."
        chunks = _build_chunks(text, "en", SplitConfiguration())
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < chunk.token_count <= 900 for chunk in chunks))

    def test_small_final_chunk_is_merged_within_same_unit_when_possible(self):
        text = "CHAPTER 1\n\n" + ("evidence " * 690) + "\n\n" + ("clue " * 20)
        chunks = _build_chunks(text, "en", SplitConfiguration())
        self.assertTrue(all(chunk.token_count >= 400 for chunk in chunks))
        self.assertTrue(all(chunk.unit_title == chunks[0].unit_title for chunk in chunks))

    def test_second_unchanged_split_is_already_current_and_preserves_identity(self):
        first = split_work(work=self.work)
        before = self._snapshot()
        files_before = set(Path(MEDIA_ROOT).rglob("chunk*.txt"))
        second = split_work(work=self.work)
        self.assertEqual(first.chunk_count, second.chunk_count)
        self.assertEqual(second.outcome, SplitOutcome.ALREADY_CURRENT.value)
        self.assertEqual(before, self._snapshot())
        self.assertEqual(files_before, set(Path(MEDIA_ROOT).rglob("chunk*.txt")))
        self.assertEqual(
            WorkSplitRun.objects.filter(work=self.work, outcome=SplitOutcome.ALREADY_CURRENT.value).count(),
            1,
        )

    def test_canonical_change_causes_reprocessed(self):
        split_work(work=self.work)
        self._replace_canonical()
        result = split_work(work=self.work)
        self.assertEqual(result.outcome, SplitOutcome.REPROCESSED.value)
        self.assertEqual(self.work.split_runs.first().outcome, SplitOutcome.REPROCESSED.value)
        self.assertEqual(self.work.split_run.source_sha256, self.work.canonical_text.sha256)

    def test_chunker_version_change_causes_reprocessed(self):
        split_work(work=self.work)
        result = split_work(work=self.work, chunker_version=CHUNKER_VERSION + ".next")
        self.assertEqual(result.outcome, SplitOutcome.REPROCESSED.value)
        self.assertEqual(self.work.split_run.chunker_version, CHUNKER_VERSION + ".next")

    def test_reprocessing_removes_obsolete_files_after_commit(self):
        split_work(work=self.work)
        self._replace_canonical("A short replacement ending.")
        with self.captureOnCommitCallbacks(execute=True):
            split_work(work=self.work)
        database_files = {Path(chunk.text_file.path) for chunk in self._chunks()}
        storage_files = set(Path(MEDIA_ROOT).rglob("chunk*.txt"))
        self.assertEqual(storage_files, database_files)

    def test_failure_during_reprocessing_rolls_back_and_removes_new_files(self):
        split_work(work=self.work)
        before = self._snapshot()
        files_before = set(Path(MEDIA_ROOT).rglob("chunk*.txt"))
        self._replace_canonical("Changed evidence forces replacement. " * 100)
        original_save = WorkChunk.save
        calls = {"count": 0}

        def fail_second_save(instance, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("simulated persistence failure")
            return original_save(instance, *args, **kwargs)

        with patch.object(WorkChunk, "save", new=fail_second_save):
            with self.assertRaisesRegex(RuntimeError, "simulated persistence failure"):
                split_work(work=self.work)

        self.assertEqual(before, self._snapshot())
        self.assertEqual(files_before, set(Path(MEDIA_ROOT).rglob("chunk*.txt")))
        self.assertEqual(self.work.split_runs.first().outcome, SplitOutcome.FAILED.value)
        self.assertEqual(self.work.split_run.status, SplitStatus.COMPLETED.value)

    def test_concurrent_canonical_sha_change_fails_and_preserves_previous_set(self):
        split_work(work=self.work)
        before = self._snapshot()

        def change_sha():
            canonical = self.work.canonical_text
            canonical.sha256 = "0" * 64
            canonical.save(update_fields=["sha256", "updated_at"])

        with self.assertRaises(CanonicalChangedDuringSplit):
            split_work(
                work=self.work,
                chunker_version=CHUNKER_VERSION + ".concurrent",
                _before_persist=change_sha,
            )
        self.assertEqual(before, self._snapshot())
        failed = self.work.split_runs.first()
        self.assertEqual(failed.status, SplitRunStatus.FAILED.value)
        self.assertEqual(failed.outcome, SplitOutcome.FAILED.value)

    def test_unknown_units_keep_line_traceability_and_canonical_origin(self):
        text = "M.R.C.S.\n\nL\n\nFULL TEXT\n\nA narrative without a reliable chapter heading."
        chunks = _build_chunks(text, "en", SplitConfiguration())
        self.assertTrue(chunks)
        self.assertTrue(all(chunk.unit_type == "unknown" for chunk in chunks))
        self.assertTrue(all(chunk.start_line <= chunk.end_line for chunk in chunks))

    def test_completed_split_unlocks_processing_and_embeddings_tabs(self):
        split_work(work=self.work)
        processing = self.client.get(
            reverse("author_studio:author_processing", args=[self.work.author.slug])
        )
        self.assertContains(processing, "Prosseguir para etapa 02")
        embeddings = self.client.get(
            reverse("author_studio:author_embeddings", args=[self.work.author.slug])
        )
        self.assertContains(embeddings, "Entrada preparada")
        self.assertContains(embeddings, "Gerar embeddings")
