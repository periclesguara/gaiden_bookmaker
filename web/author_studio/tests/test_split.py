import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gaiden.application.author_studio.create_author import create_author
from gaiden.application.author_studio.ingest_work_source import ingest_new_work
from gaiden.application.author_studio.split_work import split_work
from gaiden.domain.author_studio.enums import SplitStatus

from ..models import WorkChunk, WorkSplit

MEDIA_ROOT = tempfile.mkdtemp(prefix="gaiden-author-studio-split-tests-")


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class WorkSplitTests(TestCase):
    def setUp(self):
        author = create_author("Arthur Conan Doyle")
        paragraphs = [
            f"Paragraph {number}. " + "Sherlock Holmes examined the evidence with careful attention. " * 35
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

    def test_split_persists_ordered_chunks_and_run_status(self):
        result = split_work(work=self.work)
        run = WorkSplit.objects.get(work=self.work)
        self.assertEqual(run.status, SplitStatus.COMPLETED.value)
        self.assertEqual(run.chunk_count, result.chunk_count)
        self.assertGreater(result.chunk_count, 1)
        first = WorkChunk.objects.filter(work=self.work).first()
        self.assertEqual(first.code, f"{self.work.code}-CHK0001")
        self.assertTrue(first.text_file.storage.exists(first.text_file.name))

    def test_reprocessing_replaces_chunks_instead_of_duplicating(self):
        first = split_work(work=self.work)
        second = split_work(work=self.work)
        self.assertEqual(first.chunk_count, second.chunk_count)
        self.assertEqual(WorkChunk.objects.filter(work=self.work).count(), second.chunk_count)

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
