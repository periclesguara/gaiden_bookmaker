import hashlib
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from gaiden.application.author_studio.create_author import create_author
from gaiden.application.author_studio.create_work import create_work
from gaiden.application.author_studio.ingest_work_source import ingest_work_source, store_work_source
from gaiden.domain.author_studio.enums import CanonicalTextStatus, SourceStatus
from gaiden.domain.author_studio.exceptions import DuplicateSourceError, InvalidSourceError

from ..models import CanonicalText, WorkSource

MEDIA_ROOT = tempfile.mkdtemp(prefix="gaiden-author-studio-tests-")


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class SourceIngestionTests(TestCase):
    def setUp(self):
        author = create_author("Arthur Conan Doyle")
        self.work = create_work(author=author, title="The Adventures of Sherlock Holmes")

    def upload(self, name="book.txt", content=b"CHAPTER 1\nA narrative body.", content_type="text/plain"):
        return SimpleUploadedFile(name, content, content_type=content_type)

    def test_valid_upload_checksum_storage_and_code(self):
        payload = b"CHAPTER 1\nA narrative body."
        source = store_work_source(work=self.work, upload=self.upload(content=payload))
        self.assertEqual(source.code, "ACD-ADVEN-SRC001")
        self.assertEqual(source.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(source.size_bytes, len(payload))
        self.assertTrue(source.stored_file.storage.exists(source.stored_file.name))
        self.assertIn("author_studio/authors/ACD/works/ACD-ADVEN/sources/", source.stored_file.name)

    def test_duplicate_file_for_same_work_is_blocked(self):
        store_work_source(work=self.work, upload=self.upload())
        with self.assertRaisesRegex(DuplicateSourceError, "já foi enviado"):
            store_work_source(work=self.work, upload=self.upload())
        self.assertEqual(WorkSource.objects.count(), 1)

    def test_codes_are_sequential_per_work(self):
        one = store_work_source(work=self.work, upload=self.upload(content=b"CHAPTER 1\nFirst narrative."))
        two = store_work_source(work=self.work, upload=self.upload(name="second.txt", content=b"CHAPTER 1\nSecond narrative."))
        self.assertEqual(one.code, "ACD-ADVEN-SRC001")
        self.assertEqual(two.code, "ACD-ADVEN-SRC002")

    def test_invalid_and_unsupported_extensions_are_clear(self):
        with self.assertRaisesRegex(InvalidSourceError, "Formato não suportado"):
            store_work_source(work=self.work, upload=self.upload(name="payload.exe"))

    def test_malformed_zip_is_rejected(self):
        with self.assertRaisesRegex(InvalidSourceError, "ZIP malformado"):
            store_work_source(work=self.work, upload=self.upload(name="broken.zip", content=b"not-a-zip", content_type="application/zip"))

    def test_accepted_format_without_extractor_is_explicitly_unsupported(self):
        upload = self.upload(name="book.rtf", content=b"{\\rtf1 A valid RTF source}", content_type="application/rtf")
        result = ingest_work_source(work=self.work, upload=upload)
        result.source.refresh_from_db()
        self.assertEqual(result.source.extraction_status, SourceStatus.UNSUPPORTED_EXTRACTION)
        self.assertIsNone(result.canonical_text)

    def test_text_ingestion_creates_canonical_text(self):
        body = " ".join(["This preserved narrative sentence contains literary content and dialogue."] * 25)
        upload = self.upload(content=f"TITLE\nPREFACE\nRemove this.\nCHAPTER 1\n{body}".encode())
        result = ingest_work_source(work=self.work, upload=upload)
        self.assertIsNotNone(result.canonical_text)
        canonical = CanonicalText.objects.get(work=self.work)
        self.assertEqual(canonical.code, "ACD-ADVEN-CAN001")
        self.assertEqual(canonical.status, CanonicalTextStatus.READY)
        self.assertGreater(canonical.word_count, 100)
        with canonical.text_file.open("r") as handle:
            text = handle.read()
        self.assertIn("CHAPTER 1", text)
        self.assertNotIn("PREFACE", text)

    def test_empty_upload_is_rejected(self):
        with self.assertRaisesRegex(InvalidSourceError, "vazio"):
            store_work_source(work=self.work, upload=self.upload(content=b""))

    def test_extensionless_pdf_is_accepted_from_safe_mime_and_signature(self):
        source = store_work_source(
            work=self.work,
            upload=self.upload(name="source", content=b"%PDF-1.4\nminimal", content_type="application/pdf"),
        )
        self.assertEqual(source.extension, ".pdf")

    def test_mime_extension_mismatch_is_rejected(self):
        with self.assertRaisesRegex(InvalidSourceError, "MIME type"):
            store_work_source(
                work=self.work,
                upload=self.upload(name="source.pdf", content=b"%PDF-1.4\nminimal", content_type="text/plain"),
            )

    def test_invalid_upload_does_not_leave_a_new_work(self):
        from gaiden.application.author_studio.ingest_work_source import ingest_new_work

        with self.assertRaises(InvalidSourceError):
            ingest_new_work(
                author=self.work.author,
                title="A New Invalid Work",
                original_language="en",
                upload=self.upload(name="malware.exe"),
            )
        self.assertFalse(self.work.author.works.filter(title="A New Invalid Work").exists())
