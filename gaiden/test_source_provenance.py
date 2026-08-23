import hashlib
import io
import unittest
import zipfile

from gaiden.source_provenance import extract_source_provenance_bytes


def epub_fixture() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="content.opf"/></rootfiles>
            </container>""",
        )
        archive.writestr(
            "content.opf",
            """<package xmlns:dc="http://purl.org/dc/elements/1.1/">
              <metadata>
                <dc:title>The Secret of Chimneys</dc:title>
                <dc:creator>Christie, Agatha</dc:creator>
                <dc:language>en</dc:language>
                <dc:identifier>https://www.gutenberg.org/ebooks/65238</dc:identifier>
                <dc:subject>Detective fiction</dc:subject>
                <dc:rights>Public domain in the USA.</dc:rights>
                <meta property="dcterms:modified">2026-03-01T00:00:00Z</meta>
              </metadata>
            </package>""",
        )
        archive.writestr(
            "text/title.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body>
              <p>Release Date: May 3, 2021</p>
              <p>Credits: Distributed <a>Proofreading Team</a></p>
              <p>Copyright, 1925, by Agatha Christie</p>
              <p>Reading level: 8</p><p>Downloads: 999</p>
            </body></html>""",
        )
    return stream.getvalue()


class SourceProvenanceTests(unittest.TestCase):
    def test_extracts_stable_epub_metadata(self):
        data = epub_fixture()
        record = extract_source_provenance_bytes(data, "chimneys.epub")

        self.assertEqual(record["original_title"], "The Secret of Chimneys")
        self.assertEqual(record["source_author"], "Agatha Christie")
        self.assertEqual(record["original_publication_year"], 1925)
        self.assertEqual(record["original_publication_basis"], "copyright_notice")
        self.assertEqual(record["source_platform"], "Project Gutenberg")
        self.assertEqual(record["source_identifier"], "65238")
        self.assertEqual(record["source_url"], "https://www.gutenberg.org/ebooks/65238")
        self.assertEqual(record["source_release_date"], "2021-05-03")
        self.assertEqual(record["source_language"], "en")
        self.assertIn("Distributed Proofreading Team", record["source_credits"])
        self.assertEqual(record["source_sha256"], hashlib.sha256(data).hexdigest())
        self.assertNotIn("downloads", record)
        self.assertNotIn("reading_level", record)
        self.assertNotIn("dcterms:modified", str(record))
        self.assertNotIn("2026", str(record))

    def test_extracts_project_gutenberg_text_header(self):
        data = b"""Title: Persuasion\nAuthor: Jane Austen\nRelease Date: June 5, 2008 [EBook #105]\nLanguage: English\nCredits: A volunteer team\nCopyright 1818\n"""
        record = extract_source_provenance_bytes(data, "persuasion.txt")

        self.assertEqual(record["original_title"], "Persuasion")
        self.assertEqual(record["source_author"], "Jane Austen")
        self.assertEqual(record["original_publication_year"], 1818)
        self.assertEqual(record["source_identifier"], "105")
        self.assertEqual(record["source_release_date"], "2008-06-05")

    def test_failure_keeps_filename_and_hash(self):
        data = b"not an epub"
        record = extract_source_provenance_bytes(data, "broken.epub")

        self.assertEqual(record["source_filename"], "broken.epub")
        self.assertEqual(record["source_sha256"], hashlib.sha256(data).hexdigest())
        self.assertTrue(record["extraction_warnings"])

    def test_unsupported_format_keeps_auditable_identity(self):
        data = b"opaque"
        record = extract_source_provenance_bytes(data, "source.pdf")

        self.assertEqual(record["source_filename"], "source.pdf")
        self.assertEqual(record["source_sha256"], hashlib.sha256(data).hexdigest())
        self.assertIn("Unsupported", record["extraction_warnings"][0])


if __name__ == "__main__":
    unittest.main()
