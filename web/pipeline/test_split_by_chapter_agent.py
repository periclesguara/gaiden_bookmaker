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
from gaiden.chapter_agent_split import (
    rewrite_single_chapter_parts,
    split_merged_text_into_chapters,
    write_chapter_split_artifacts,
)
from pipeline.services import canonical_merge, chapter_agent, paths
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

    def test_write_chapter_split_artifacts_creates_one_part_per_chapter_by_default(self):
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
            self.assertEqual(manifest["parts_per_chapter"], 1)
            self.assertEqual(len(manifest["chapters"][0]["parts"]), 1)
            self.assertTrue(manifest_path.exists())
            for part in manifest["chapters"][0]["parts"]:
                self.assertTrue((root / "parts" / part["filename"]).exists())

    def test_write_chapter_split_artifacts_accepts_two_parts_per_chapter_when_requested(self):
        merged_text = (
            "## Chapter 1 - The Gate\n\n"
            + ("Paragraph A.\n\n" * 12)
            + ("Paragraph B.\n\n" * 12)
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = write_chapter_split_artifacts(
                merged_text,
                root / "parts",
                parts_per_chapter=2,
            )

            self.assertEqual(manifest["parts_per_chapter"], 2)
            self.assertEqual(len(manifest["chapters"][0]["parts"]), 2)

    def test_char_limited_split_never_spills_into_next_chapter(self):
        chapter_one_body = ("First chapter only marker. " * 140).strip()
        chapter_two_body = ("Second chapter only marker. " * 140).strip()
        merged_text = (
            "## Chapter 1 - The Gate\n\n"
            f"{chapter_one_body}\n\n"
            "## Chapter 2 - The Road\n\n"
            f"{chapter_two_body}\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = write_chapter_split_artifacts(
                merged_text,
                root / "parts",
                max_chars_per_part=1200,
            )

            self.assertEqual(manifest["chapter_count"], 2)
            self.assertGreater(len(manifest["chapters"][0]["parts"]), 1)
            chapter_one_files = sorted((root / "parts").glob("chapter_01_part_*.txt"))
            chapter_two_files = sorted((root / "parts").glob("chapter_02_part_*.txt"))
            self.assertTrue(chapter_one_files)
            self.assertTrue(chapter_two_files)
            for path in chapter_one_files:
                text = path.read_text(encoding="utf-8")
                self.assertLessEqual(len(text), 1200)
                self.assertNotIn("## Chapter 2 - The Road", text)
                self.assertNotIn("Second chapter only marker.", text)
            for path in chapter_two_files:
                self.assertLessEqual(len(path.read_text(encoding="utf-8")), 1200)
            self.assertIn("## Chapter 2 - The Road", chapter_two_files[0].read_text(encoding="utf-8"))

    def test_split_chapter_into_parts_prefers_numbered_boundaries_when_available(self):
        merged_text = (
            "## Chapter 1 - Devotional\n\n"
            "Opening paragraph.\n\n"
            "2\\. Second numbered section.\n\n"
            "3\\. Third numbered section.\n\n"
            "4\\. Fourth numbered section.\n\n"
            "5\\. Fifth numbered section.\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = write_chapter_split_artifacts(
                merged_text,
                root / "parts",
                parts_per_chapter=4,
            )

            chapter_parts = manifest["chapters"][0]["parts"]
            self.assertEqual(len(chapter_parts), 4)
            part_paths = [root / "parts" / item["filename"] for item in chapter_parts]
            texts = [path.read_text(encoding="utf-8") for path in part_paths]
            self.assertTrue(texts[0].startswith("## Chapter 1 - Devotional"))
            self.assertTrue(texts[1].lstrip().startswith("2\\."))
            self.assertTrue(texts[2].lstrip().startswith("3\\."))
            self.assertTrue(texts[3].lstrip().startswith("5\\."))

    def test_rewrite_single_chapter_parts_can_expand_specific_chapter_to_four_parts(self):
        merged_text = (
            "## Chapter 1 - The Gate\n\n"
            + ("Paragraph A. " * 120)
            + "\n\n"
            + "## Chapter 2 - The Road\n\n"
            + ("Paragraph B. " * 240)
            + "\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            parts_dir = root / "parts"
            manifest_path = root / "manifest.json"
            write_chapter_split_artifacts(
                merged_text,
                parts_dir,
                manifest_path=manifest_path,
                parts_per_chapter=2,
            )

            result = rewrite_single_chapter_parts(
                merged_text,
                parts_dir,
                chapter_index=2,
                parts_per_chapter=4,
                manifest_path=manifest_path,
            )

            self.assertEqual(result["part_count"], 4)
            chapter_two_parts = sorted(parts_dir.glob("chapter_02_part_*.txt"))
            self.assertEqual([p.name for p in chapter_two_parts], [
                "chapter_02_part_01.txt",
                "chapter_02_part_02.txt",
                "chapter_02_part_03.txt",
                "chapter_02_part_04.txt",
            ])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["parts_per_chapter"], 4)

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
        (contract_dir / "de_refine_2026.json").write_text(
            json.dumps({"model": "gpt-5.4", "max_output_tokens": 1800, "output": {"language": "de"}}),
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
        self.assertEqual(result["part_count"], 1)
        self.assertTrue((split_root / "manifest.json").exists())
        self.assertFalse((split_root / "agent").exists())
        self.assertTrue((split_root / "parts" / "chapter_01_part_01.txt").exists())

    def test_run_split_refine_by_chapter_uses_merge_refine_as_base(self):
        build_dir = paths.edition_build_dir(self.edition)
        build_dir.mkdir(parents=True, exist_ok=True)
        merge_refine_path = build_dir / "merge_refine.txt"
        merge_refine_path.write_text(
            "## Chapter 1 - The Return\n\n"
            "Refined paragraph A.\n\n"
            "Refined paragraph B.\n",
            encoding="utf-8",
        )

        result = chapter_agent.run_split_refine_by_chapter(
            self.edition,
            max_chars_per_part=6000,
        )

        split_root = paths.split_refine_by_chapter_dir(self.edition)
        self.assertEqual(result["merge_refine_path"], str(merge_refine_path))
        self.assertEqual(result["chapter_count"], 1)
        self.assertEqual(result["part_count"], 1)
        self.assertTrue((split_root / "manifest.json").exists())
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

    def test_prepare_refine_agent_handoff_uses_split_by_chapter_parts_as_source(self):
        build_dir = paths.edition_build_dir(self.edition)
        build_dir.mkdir(parents=True, exist_ok=True)
        split_root = paths.split_by_chapter_dir(self.edition)
        parts_dir = split_root / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        (split_root / "manifest.json").write_text('{"chapter_count": 1}', encoding="utf-8")
        (parts_dir / "chapter_01_part_01.txt").write_text("part one\n", encoding="utf-8")
        (parts_dir / "chapter_01_part_02.txt").write_text("part two\n", encoding="utf-8")

        source_dir, source_label, out_dir, profile_cfg, profile = pipeline_views._prepare_refine_agent_handoff(
            self.edition,
            "en",
            refine_profile="ingles_neutro",
        )

        source_files = sorted(p.name for p in source_dir.glob("*.txt"))
        self.assertEqual(source_files, ["chapter_01_part_01.txt", "chapter_01_part_02.txt"])
        self.assertEqual(source_label, "split_by_chapter/parts")
        self.assertEqual(out_dir, split_root / "return_aldebaran")
        self.assertEqual(profile_cfg["agent_name"], "Aldebaran")
        self.assertEqual(profile, "ingles_neutro")

    def test_prepare_refine_agent_handoff_uses_return_kaiser_for_german(self):
        build_dir = paths.edition_build_dir(self.edition)
        build_dir.mkdir(parents=True, exist_ok=True)
        split_root = paths.split_by_chapter_dir(self.edition)
        parts_dir = split_root / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        (split_root / "manifest.json").write_text('{"chapter_count": 1}', encoding="utf-8")
        (parts_dir / "chapter_01_part_01.txt").write_text("teil eins\n", encoding="utf-8")

        source_dir, _source_label, out_dir, profile_cfg, _profile = pipeline_views._prepare_refine_agent_handoff(
            self.edition,
            "de",
            refine_profile="de_kaiser",
        )

        self.assertEqual(profile_cfg["agent_name"], "Kaiser")
        self.assertEqual(source_dir, parts_dir)
        self.assertEqual(out_dir, split_root / "return_kaiser")

    def test_resolve_polish_source_dir_prefers_split_refine_by_chapter_parts(self):
        split_root = paths.split_refine_by_chapter_dir(self.edition)
        parts_dir = split_root / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        (split_root / "manifest.json").write_text('{"chapter_count": 1}', encoding="utf-8")
        (parts_dir / "chapter_01_part_01.txt").write_text("polish source\n", encoding="utf-8")

        source_dir, source_label = pipeline_views._resolve_polish_source_dir(self.edition)
        out_dir = pipeline_views._resolve_polish_output_dir(source_dir, self.edition)

        self.assertEqual(source_dir, parts_dir)
        self.assertEqual(source_label, "split_refine_by_chapter/parts")
        self.assertEqual(out_dir, split_root / "return_english_polidor")
        custom_out_dir = pipeline_views._resolve_polish_output_dir(
            source_dir,
            self.edition,
            agent_name="Alamaguederaz",
        )
        self.assertEqual(custom_out_dir, split_root / "return_alamaguederaz")

    def test_recommended_split_parts_for_philosophy_and_devotional_english(self):
        self.assertEqual(pipeline_views._recommended_split_parts_for_translate_variant("en_philo"), 4)
        self.assertEqual(pipeline_views._recommended_split_parts_for_translate_variant("en_devotional"), 4)
        self.assertEqual(pipeline_views._recommended_split_parts_for_translate_variant("en"), 1)

    def test_canonical_merge_localizes_german_chapter_headings_for_book_0002(self):
        refine_dir = self.temp_root / "data" / "builds" / "book_0002" / "de" / "split_by_chapter" / "return_kaiser"
        refine_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = refine_dir / "chapter_01_part_01.txt"
        chunk_path.write_text(
            "## Chapter 1 The Science of Deduction\n\nHolmes saß ruhig da.\n",
            encoding="utf-8",
        )

        changed = canonical_merge.localize_chapter_headings_in_place(
            refine_dir,
            book_code="book_0002",
            language="de",
        )

        self.assertEqual(changed, 1)
        updated = chunk_path.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith("## Kapitel 1 - Die Wissenschaft der Deduktion"))
