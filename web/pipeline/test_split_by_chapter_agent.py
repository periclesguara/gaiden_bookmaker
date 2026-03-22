import tempfile
from pathlib import Path
import sys
import json

from django.test import TestCase
from django.test.utils import override_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from editorial.models import Contributor, Edition, Language, Seal, Work
from gaiden.chapter_agent_split import split_merged_text_into_chapters, write_chapter_split_artifacts
from pipeline.services import chapter_agent, paths
from pipeline import views as pipeline_views


class ChapterAgentSplitLogicTests(TestCase):
    def test_split_merged_text_detects_marked_chapters(self):
        merged_text = (
            "## Chapter 1 - The Gate\n\n"
            + ("Paragraph A. " * 80)
            + "\n\n"
            "## Chapter 2 - The Road\n\n"
            + ("Paragraph C. " * 80)
            + "\n"
        )

        chapters = split_merged_text_into_chapters(merged_text)

        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["heading"], "## Chapter 1 - The Gate")
        self.assertIn("Paragraph C.", chapters[1]["text"])

    def test_write_chapter_split_artifacts_creates_four_parts_per_chapter(self):
        merged_text = (
            "## Chapter 1 - The Gate\n\n"
            "Paragraph A.\n\n"
            "Paragraph B.\n\n"
            "Paragraph C.\n\n"
            "Paragraph D.\n\n"
            "Paragraph E.\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "manifest.json"
            manifest = write_chapter_split_artifacts(
                merged_text,
                root / "parts",
                manifest_path=manifest_path,
            )

            self.assertEqual(manifest["chapter_count"], 1)
            self.assertEqual(len(manifest["chapters"][0]["parts"]), 4)
            self.assertTrue(manifest_path.exists())
            for part in manifest["chapters"][0]["parts"]:
                self.assertTrue((root / "parts" / part["filename"]).exists())

    def test_split_merged_text_ignores_toc_noise_and_false_numeric_headings(self):
        merged_text = (
            "Chapter VII—The Trapping of Birdy Edwards\n\n"
            "Epilogue\n"
            "Illustrations\n"
            "\"What's this, Mr. Holmes?\"\n\n"
            "Chapter I\n\n"
            "The Warning\n"
            + ("Body one. " * 120)
            + "\n\nCHAPTER 171\n\n"
            "noise\n\n"
            "Chapter II\n\n"
            + ("Body two. " * 120)
            + "\n\nChapter III\n\n"
            + ("Body three. " * 120)
            + "\n\nChapter IV\n\n"
            + ("Body four. " * 120)
            + "\n\nChapter I\n\n"
            + ("Part two body one. " * 120)
            + "\n\nChapter II\n\n"
            + ("Part two body two. " * 120)
            + "\n\nChapter III\n\n"
            + ("Part two body three. " * 120)
            + "\n\nChapter IV\n\n"
            + ("Part two body four. " * 120)
            + "\n\nEpilogue\n\n"
            + ("Final body. " * 120)
        )

        chapters = split_merged_text_into_chapters(merged_text)

        headings = [item["heading"] for item in chapters]
        self.assertEqual(
            headings,
            [
                "Chapter 1",
                "Chapter 2",
                "Chapter 3",
                "Chapter 4",
                "Chapter 1",
                "Chapter 2",
                "Chapter 3",
                "Chapter 4",
                "Epilogue",
            ],
        )

    def test_split_merged_text_converts_roman_chapter_numbers_to_arabic(self):
        merged_text = (
            "Chapter I—The Warning\n\n"
            + ("Body one. " * 120)
            + "\n\nChapter II: The End\n\n"
            + ("Body two. " * 120)
        )

        chapters = split_merged_text_into_chapters(merged_text)

        self.assertEqual(chapters[0]["heading"], "Chapter 1—The Warning")
        self.assertTrue(chapters[0]["text"].startswith("Chapter 1—The Warning"))
        self.assertEqual(chapters[1]["heading"], "Chapter 2: The End")


class ChapterAgentServiceTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        (self.temp_root / "web").mkdir(parents=True, exist_ok=True)
        self.override = override_settings(BASE_DIR=str(self.temp_root / "web"))
        self.override.enable()

        self.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
            is_active=True,
        )
        self.author = Contributor.objects.create(name="Author Test")
        self.seal = Seal.objects.create(slug="seal-test", name="Seal Test")
        self.work = Work.objects.create(
            code="book_0104",
            title="Book Test",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )
        contract_dir = self.temp_root / "gaiden" / "contracts" / "refine"
        contract_dir.mkdir(parents=True, exist_ok=True)
        (contract_dir / "en_refine_2025.json").write_text(
            json.dumps({"model": "gpt-5.4", "max_output_tokens": 1800, "output": {"language": "en"}}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.override.disable()
        self.temp_dir.cleanup()

    def test_run_split_by_chapter_uses_merge_translate_as_base_and_writes_only_parts(self):
        build_dir = paths.edition_build_dir(self.edition)
        build_dir.mkdir(parents=True, exist_ok=True)
        merge_translate_path = build_dir / "merge_translate.txt"
        merge_translate_path.write_text(
            "## Chapter 1 - The Gate\n\n"
            "Paragraph A.\n\n"
            "Paragraph B.\n\n"
            "Paragraph C.\n\n"
            "Paragraph D.\n\n"
            "Paragraph E.\n",
            encoding="utf-8",
        )
        result = chapter_agent.run_split_by_chapter(self.edition)

        split_root = paths.split_by_chapter_dir(self.edition)
        self.assertEqual(result["merge_translate_path"], str(merge_translate_path))
        self.assertEqual(result["chapter_count"], 1)
        self.assertEqual(result["part_count"], 4)
        self.assertTrue((split_root / "manifest.json").exists())
        self.assertFalse((split_root / "agent").exists())
        self.assertTrue((split_root / "parts" / "chapter_01_part_01.txt").exists())

    def test_resolve_refine_source_dir_prefers_split_by_chapter_parts(self):
        build_dir = paths.edition_build_dir(self.edition)
        build_dir.mkdir(parents=True, exist_ok=True)
        split_root = paths.split_by_chapter_dir(self.edition)
        parts_dir = split_root / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        (split_root / "manifest.json").write_text('{"chapter_count": 1}', encoding="utf-8")
        (parts_dir / "chapter_01_part_01.txt").write_text("part one\n", encoding="utf-8")

        source_dir, source_label = pipeline_views._resolve_refine_source_dir(self.edition, "en")

        self.assertEqual(source_dir, parts_dir)
        self.assertEqual(source_label, "split_by_chapter/parts")

    def test_build_runtime_refine_contract_uses_split_by_chapter_parts_as_source(self):
        build_dir = paths.edition_build_dir(self.edition)
        build_dir.mkdir(parents=True, exist_ok=True)
        split_root = paths.split_by_chapter_dir(self.edition)
        parts_dir = split_root / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        (split_root / "manifest.json").write_text('{"chapter_count": 1}', encoding="utf-8")
        (parts_dir / "chapter_01_part_01.txt").write_text("part one\n", encoding="utf-8")
        (parts_dir / "chapter_01_part_02.txt").write_text("part two\n", encoding="utf-8")

        contract_path, refine_input_dir, out_dir = pipeline_views._build_runtime_refine_contract(
            self.edition,
            "en",
            refine_profile="ingles_neutro",
        )

        payload = json.loads(contract_path.read_text(encoding="utf-8"))
        copied_files = sorted(p.name for p in refine_input_dir.glob("*.txt"))
        self.assertEqual(copied_files, ["chapter_01_part_01.txt", "chapter_01_part_02.txt"])
        self.assertEqual(payload["chunk_dir"], str(refine_input_dir))
        self.assertEqual(out_dir, split_root / "return_aldebaran")
