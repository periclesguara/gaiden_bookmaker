from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from gaiden.tools.agent_translate_default import run_agent_translate
from gaiden.translate_mode_policy import apply_skip_policy
from gaiden.translate_artifacts import (
    active_pointer_path,
    assert_valid_canonical_artifact,
    canonical_artifact_path,
    sha256_file,
    write_active_pointer,
    write_canonical_meta,
)
from gaiden.translate_engine_v1 import run_translate_safe
from pipeline.services import image_pipeline
from pipeline.services import miolo_transform
from pipeline.services import run_state_policy
from pipeline.services import text_source


class TranslateModePolicyTests(SimpleTestCase):
    def test_automatic_mode_forces_skip_to_do(self):
        policy = apply_skip_policy(
            selected_mode="automatic",
            split_mode="skip",
            refine_mode="skip",
        )
        self.assertEqual(policy["selected_mode"], "automatic")
        self.assertEqual(policy["split_mode"], "do")
        self.assertEqual(policy["refine_mode"], "do")
        self.assertTrue(policy["skip_requested"])
        self.assertFalse(policy["skip_applied"])
        self.assertTrue(policy["skip_corrected"])
        self.assertEqual(policy["skip_block_reason"], "automatic_mode")

    def test_default_mode_allows_skip(self):
        policy = apply_skip_policy(
            selected_mode="default",
            split_mode="skip",
            refine_mode="do",
        )
        self.assertEqual(policy["selected_mode"], "default")
        self.assertEqual(policy["split_mode"], "skip")
        self.assertEqual(policy["refine_mode"], "do")
        self.assertTrue(policy["skip_requested"])
        self.assertTrue(policy["skip_applied"])
        self.assertFalse(policy["skip_corrected"])
        self.assertIsNone(policy["skip_block_reason"])


class TranslateArtifactsHardeningTests(SimpleTestCase):
    def test_active_pointer_write_is_atomic_and_validated(self):
        with TemporaryDirectory() as td:
            out_dir = Path(td)
            book = "book_0001"
            lang = "en_modern"
            artifact = canonical_artifact_path(out_dir, book, lang, "automatic")
            artifact.write_text(("valid line\n" * 20), encoding="utf-8")

            write_active_pointer(out_dir, book, lang, artifact.name)

            pointer = active_pointer_path(out_dir, book, lang)
            self.assertTrue(pointer.exists())
            self.assertEqual(pointer.read_text(encoding="utf-8").strip(), artifact.name)
            self.assertFalse(Path(str(pointer) + ".tmp").exists())

    def test_canonical_artifact_validation_rejects_placeholders(self):
        with TemporaryDirectory() as td:
            bad = Path(td) / "book_0001_en_automatic_merge_clean.txt"
            bad.write_text("[ERROR]\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                assert_valid_canonical_artifact(bad)


class TranslatePipelineOperationalTests(SimpleTestCase):
    def _create_chunk(self, root: Path, book: str, source_lang: str, content: str) -> Path:
        chunk = root / book / source_lang / "ch_001_chunk_001.txt"
        chunk.parent.mkdir(parents=True, exist_ok=True)
        chunk.write_text(content, encoding="utf-8")
        return chunk

    @patch("gaiden.openai_client.openai_healthcheck", return_value=(True, None))
    @patch("gaiden.translate_engine_v1.translate_book_chunks")
    def test_case1_automatic_success(self, mock_translate_book_chunks, _mock_healthcheck):
        with TemporaryDirectory() as td:
            root = Path(td)
            chunks_root = root / "chunks"
            translated_root = root / "translated"
            book = "book_0123"
            target_lang = "en_modern"
            self._create_chunk(chunks_root, book, "en", "Source line\n" * 80)

            def _fake_translate_book_chunks(**_kwargs):
                out_dir = translated_root / book / target_lang
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / "ch_001_chunk_001.en_modern.txt"
                out.write_text("Translated line\n" * 90, encoding="utf-8")
                meta = out_dir / "ch_001_chunk_001.en_modern.meta.json"
                meta.write_text("{}", encoding="utf-8")
                return {
                    "status": "ok",
                    "validation_ratio_min": 0.95,
                    "validation_ratio_max": 1.05,
                    "items": [
                        {
                            "status": "translated",
                            "output_path": str(out),
                            "meta_path": str(meta),
                            "ratio": 1.0,
                        }
                    ],
                }

            mock_translate_book_chunks.side_effect = _fake_translate_book_chunks

            result = run_translate_safe(
                book_id=book,
                chunk_dir=chunks_root / book / "en",
                out_dir=translated_root / book / target_lang,
                suffix=target_lang,
                dry_run=False,
            )

            artifact = canonical_artifact_path(translated_root / book / target_lang, book, target_lang, "automatic")
            pointer = active_pointer_path(translated_root / book / target_lang, book, target_lang)

            self.assertEqual(result["status"], "ok_official")
            self.assertEqual(result["selected_mode"], "automatic")
            self.assertEqual(result["effective_route"], "automatic")
            self.assertFalse(result["fallback_used"])
            self.assertTrue(artifact.exists())
            self.assertEqual(pointer.read_text(encoding="utf-8").strip(), artifact.name)

    @patch("gaiden.openai_client.openai_healthcheck", return_value=(True, None))
    @patch("gaiden.translate_engine_v1.subprocess.run")
    @patch("gaiden.translate_engine_v1.translate_book_chunks", side_effect=RuntimeError("finish_reason=content_filter"))
    def test_case2_automatic_policy_block_fallback(self, _mock_translate, mock_subprocess_run, _mock_healthcheck):
        with TemporaryDirectory() as td:
            root = Path(td)
            chunks_root = root / "chunks"
            translated_root = root / "translated"
            book = "book_0456"
            target_lang = "en_modern"
            self._create_chunk(chunks_root, book, "en", "Source line\n" * 80)

            def _fake_subprocess_run(cmd, **_kwargs):
                out_dir = Path(cmd[cmd.index("--out-dir") + 1])
                suffix = cmd[cmd.index("--suffix") + 1]
                book_id = cmd[cmd.index("--book-id") + 1]
                out_dir.mkdir(parents=True, exist_ok=True)
                translated_chunk = out_dir / f"ch_001_chunk_001.{suffix}.txt"
                translated_chunk.write_text("Fallback line\n" * 90, encoding="utf-8")
                artifact = canonical_artifact_path(out_dir, book_id, suffix, "default")
                artifact.write_text("Fallback line\n" * 90, encoding="utf-8")
                write_canonical_meta(
                    artifact,
                    route="default",
                    artifact_sha256=sha256_file(artifact),
                    input_source_hash="fake-input-hash",
                )
                write_active_pointer(out_dir, book_id, suffix, artifact.name)
                report = {
                    "status": "ok",
                    "merged_txt": str(artifact),
                    "merged_len": artifact.stat().st_size,
                    "merged_count": 1,
                }
                (out_dir / "agent_translate_run_report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            mock_subprocess_run.side_effect = _fake_subprocess_run

            result = run_translate_safe(
                book_id=book,
                chunk_dir=chunks_root / book / "en",
                out_dir=translated_root / book / target_lang,
                suffix=target_lang,
                dry_run=False,
            )

            artifact = canonical_artifact_path(translated_root / book / target_lang, book, target_lang, "default")
            pointer = active_pointer_path(translated_root / book / target_lang, book, target_lang)

            self.assertEqual(result["status"], "ok_fallback")
            self.assertTrue(result["fallback_used"])
            self.assertEqual(result["selected_mode"], "automatic")
            self.assertEqual(result["effective_route"], "default")
            self.assertEqual(result["final_mode"], "default")
            self.assertTrue(artifact.exists())
            self.assertEqual(pointer.read_text(encoding="utf-8").strip(), artifact.name)

    @patch("gaiden.openai_client.openai_healthcheck", return_value=(True, None))
    @patch("gaiden.tools.agent_translate_default._call_agent", return_value=("Default line\n" * 90, {}))
    def test_case3_default_direct_mode(self, _mock_call_agent, _mock_healthcheck):
        with TemporaryDirectory() as td:
            root = Path(td)
            chunk_dir = root / "chunks" / "book_0789" / "en"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            (chunk_dir / "ch_001_chunk_001.txt").write_text("Source line\n" * 80, encoding="utf-8")

            out_dir = root / "translated" / "book_0789" / "en_modern"
            result = run_agent_translate(
                book_id="book_0789",
                chunk_dir=chunk_dir,
                out_dir=out_dir,
                suffix="en_modern",
                mode="default",
            )

            artifact = canonical_artifact_path(out_dir, "book_0789", "en_modern", "default")
            pointer = active_pointer_path(out_dir, "book_0789", "en_modern")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["selected_mode"], "default")
            self.assertEqual(result["effective_route"], "default")
            self.assertTrue(artifact.exists())
            self.assertEqual(pointer.read_text(encoding="utf-8").strip(), artifact.name)

    @patch("gaiden.openai_client.openai_healthcheck", return_value=(False, "preflight down"))
    def test_case4_preflight_failure_no_artifact(self, _mock_healthcheck):
        with TemporaryDirectory() as td:
            root = Path(td)
            chunks_root = root / "chunks"
            translated_root = root / "translated"
            book = "book_0999"
            target_lang = "en_modern"
            self._create_chunk(chunks_root, book, "en", "Source line\n" * 80)
            out_dir = translated_root / book / target_lang

            with self.assertRaises(RuntimeError):
                run_translate_safe(
                    book_id=book,
                    chunk_dir=chunks_root / book / "en",
                    out_dir=out_dir,
                    suffix=target_lang,
                    dry_run=False,
                )

            report = json.loads((out_dir / "translate_safe_run_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report.get("status"), "error_preflight")
            self.assertEqual(report.get("exit_code"), 2)
            self.assertFalse(any(out_dir.glob("*_merge_clean.txt")))
            self.assertFalse(active_pointer_path(out_dir, book, target_lang).exists())

    @patch("gaiden.openai_client.openai_healthcheck", return_value=(True, None))
    @patch("gaiden.translate_engine_v1.translate_book_chunks")
    @patch("gaiden.tools.agent_translate_default._call_agent", return_value=("Default second run\n" * 90, {}))
    def test_case5_rapid_consecutive_runs_pointer_last_success(
        self,
        _mock_call_agent,
        mock_translate_book_chunks,
        _mock_healthcheck,
    ):
        with TemporaryDirectory() as td:
            root = Path(td)
            chunks_root = root / "chunks"
            translated_root = root / "translated"
            book = "book_0888"
            target_lang = "en_modern"
            self._create_chunk(chunks_root, book, "en", "Source line\n" * 80)

            def _fake_translate_book_chunks(**_kwargs):
                out_dir = translated_root / book / target_lang
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "ch_001_chunk_001.en_modern.txt").write_text(
                    "Automatic first run\n" * 90, encoding="utf-8"
                )
                return {"status": "ok", "validation_ratio_min": 0.95, "validation_ratio_max": 1.05, "items": []}

            mock_translate_book_chunks.side_effect = _fake_translate_book_chunks

            run_translate_safe(
                book_id=book,
                chunk_dir=chunks_root / book / "en",
                out_dir=translated_root / book / target_lang,
                suffix=target_lang,
                dry_run=False,
            )
            run_agent_translate(
                book_id=book,
                chunk_dir=chunks_root / book / "en",
                out_dir=translated_root / book / target_lang,
                suffix=target_lang,
                mode="default",
            )

            pointer = active_pointer_path(translated_root / book / target_lang, book, target_lang)
            automatic = canonical_artifact_path(
                translated_root / book / target_lang, book, target_lang, "automatic"
            )
            default = canonical_artifact_path(
                translated_root / book / target_lang, book, target_lang, "default"
            )

            self.assertTrue(automatic.exists())
            self.assertTrue(default.exists())
            self.assertEqual(pointer.read_text(encoding="utf-8").strip(), default.name)


class ImagePipelineDeterministicTests(SimpleTestCase):
    def test_numeric_filename_validation(self):
        self.assertTrue(image_pipeline.validate_numeric_image_filename("1.png"))
        self.assertTrue(image_pipeline.validate_numeric_image_filename("00.png"))
        self.assertTrue(image_pipeline.validate_numeric_image_filename("001.png"))
        self.assertTrue(image_pipeline.validate_numeric_image_filename("01.webp"))
        self.assertTrue(image_pipeline.validate_numeric_image_filename("02.jfif"))
        self.assertTrue(image_pipeline.validate_numeric_image_filename("image_1.png"))
        self.assertTrue(image_pipeline.validate_numeric_image_filename("image1.png"))
        self.assertTrue(image_pipeline.validate_numeric_image_filename("cover-022.jpg"))
        self.assertTrue(image_pipeline.validate_numeric_image_filename("chapter_01_img_02.png"))
        self.assertFalse(image_pipeline.validate_numeric_image_filename("01.txt"))

    def test_deterministic_insertion_is_idempotent(self):
        md = "# Chapter 1\n\nBody 1\n\n# Chapter 2\n\nBody 2\n"
        first, inserted_1, warnings_1 = image_pipeline.insert_images_deterministically(md, [0, 1, 2])
        second, inserted_2, warnings_2 = image_pipeline.insert_images_deterministically(first, [0, 1, 2])

        self.assertEqual(inserted_1, 3)
        self.assertEqual(inserted_2, 3)
        self.assertEqual(warnings_1, [])
        self.assertEqual(warnings_2, [])
        self.assertEqual(first, second)
        self.assertEqual(first.count("GAIDEN_IMAGE_INSERT_START 00"), 1)
        self.assertEqual(first.count("GAIDEN_IMAGE_INSERT_START 01"), 1)
        self.assertEqual(first.count("GAIDEN_IMAGE_INSERT_START 02"), 1)
        self.assertIn("![](images/00.jpg)", first)
        self.assertIn("![](images/01.jpg)", first)
        self.assertIn("![](images/02.jpg)", first)

    def test_missing_chapter_appends_and_warns(self):
        md = "# Chapter 1\n\nBody 1\n"
        out, inserted, warnings = image_pipeline.insert_images_deterministically(md, [3])
        self.assertEqual(inserted, 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("REPORT_V2_DEBUG", warnings[0])
        self.assertTrue(out.strip().endswith("<!-- GAIDEN_IMAGE_INSERT_END 03 -->"))

    def test_convert_raw_to_processed_and_skip_on_same_checksum(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow nao instalado")

        with TemporaryDirectory() as td:
            root = Path(td)
            raw_dir = root / "data" / "images" / "book_0001" / "en" / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)

            Image.new("RGBA", (10, 10), (10, 20, 30, 128)).save(raw_dir / "00.png")
            Image.new("RGB", (10, 10), (220, 150, 40)).save(raw_dir / "01.webp")

            with patch("pipeline.services.image_pipeline.project_root", return_value=root):
                first = image_pipeline.convert_raw_images_to_processed("book_0001", "en")
                second = image_pipeline.convert_raw_images_to_processed("book_0001", "en")

                processed_dir = root / "data" / "images" / "book_0001" / "en" / "processed"
                names = sorted(p.name for p in processed_dir.glob("*.jpg"))
                self.assertEqual(names, ["00.jpg", "01.jpg"])
                self.assertEqual(first["converted_count"], 2)
                self.assertEqual(second["converted_count"], 0)
                self.assertEqual(second["skipped_count"], 2)

    def test_convert_raw_preserves_processed_when_raw_empty(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow nao instalado")

        with TemporaryDirectory() as td:
            root = Path(td)
            raw_dir = root / "data" / "images" / "book_0001" / "en" / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (10, 10), (100, 120, 140)).save(raw_dir / "01.png")

            with patch("pipeline.services.image_pipeline.project_root", return_value=root):
                first = image_pipeline.convert_raw_images_to_processed("book_0001", "en")
                self.assertEqual(first["converted_count"], 1)

                for f in raw_dir.glob("*"):
                    f.unlink()

                second = image_pipeline.convert_raw_images_to_processed("book_0001", "en")
                processed_dir = root / "data" / "images" / "book_0001" / "en" / "processed"
                names = sorted(p.name for p in processed_dir.glob("*.jpg"))

                self.assertEqual(names, ["01.jpg"])
                self.assertEqual(second["raw_count"], 0)
                self.assertEqual(second["converted_count"], 0)
                self.assertTrue(second.get("preserved_existing"))
                self.assertEqual(second.get("reason"), "raw_empty")

    def test_convert_cover_uses_legacy_cover_when_original_missing(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow nao instalado")

        with TemporaryDirectory() as td:
            root = Path(td)
            cover_dir = root / "data" / "covers" / "book_0001" / "en"
            cover_dir.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), (12, 34, 56)).save(cover_dir / "cover.png")

            with patch("pipeline.services.image_pipeline.project_root", return_value=root):
                result = image_pipeline.convert_cover_to_jpg("book_0001", "en")

            self.assertTrue(result["cover_jpg_path"].endswith("data/covers/book_0001/en/cover.jpg"))
            self.assertTrue((cover_dir / "cover.jpg").exists())

    def test_find_cover_source_falls_back_to_other_language_folder(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            other_lang_dir = root / "data" / "covers" / "book_0001" / "pt-br"
            other_lang_dir.mkdir(parents=True, exist_ok=True)
            (other_lang_dir / "cover.jpg").write_bytes(b"legacy")
            with patch("pipeline.services.image_pipeline.project_root", return_value=root):
                found = image_pipeline.find_cover_source("book_0001", "en")
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found.name, "cover.jpg")


class RunStatePolicyPersistenceTests(SimpleTestCase):
    def test_default_skip_persists_without_reset(self):
        state = SimpleNamespace(
            selected_mode="default",
            effective_mode="default",
            split_mode="skip",
            refine_mode="skip",
        )
        first = run_state_policy.resolve_policy_from_state(
            state,
            fallback_selected_mode="automatic",
        )
        run_state_policy.apply_policy_to_state(state, first)

        second = run_state_policy.resolve_policy_from_state(
            state,
            fallback_selected_mode="automatic",
        )
        run_state_policy.apply_policy_to_state(state, second)

        self.assertEqual(second["effective_mode"], "default")
        self.assertEqual(second["split_mode"], "skip")
        self.assertEqual(second["refine_mode"], "skip")


class MioloIdempotencyTests(SimpleTestCase):
    def _sha(self, path: Path) -> str:
        import hashlib

        digest = hashlib.sha256()
        digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_md_idempotent_skip_when_source_hash_matches(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "active_merge.txt"
            source.write_text("CHAPTER 1\nFirst line.\n", encoding="utf-8")
            miolo_path = root / "MIOL_TERM.v1.md"
            published_path = root / "published_miolo.md"
            edition = SimpleNamespace(
                language=SimpleNamespace(code="en"),
                work=SimpleNamespace(code="book_0001"),
            )

            with patch("pipeline.services.paths.miolo_md_path", return_value=miolo_path), patch(
                "pipeline.services.text_source.resolve_txt_source",
                return_value=SimpleNamespace(path=source),
            ), patch(
                "pipeline.services.miolo_transform.publish_miolo_for_kdp",
                return_value=published_path,
            ):
                first = miolo_transform.ensure_md_uptodate(edition, cached_source_sha256=None)
                first_hash = self._sha(miolo_path)
                second = miolo_transform.ensure_md_uptodate(
                    edition,
                    cached_source_sha256=str(first["source_sha256"]),
                )
                second_hash = self._sha(miolo_path)

            self.assertEqual(first["md_action"], "generated")
            self.assertEqual(second["md_action"], "skipped_up_to_date")
            self.assertIn("md:already_converted", second["warnings"])
            self.assertEqual(first_hash, second_hash)

    def test_md_regenerates_when_source_hash_changes(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "active_merge.txt"
            source.write_text("CHAPTER 1\nFirst line.\n", encoding="utf-8")
            miolo_path = root / "MIOL_TERM.v1.md"
            published_path = root / "published_miolo.md"
            edition = SimpleNamespace(
                language=SimpleNamespace(code="en"),
                work=SimpleNamespace(code="book_0001"),
            )

            with patch("pipeline.services.paths.miolo_md_path", return_value=miolo_path), patch(
                "pipeline.services.text_source.resolve_txt_source",
                return_value=SimpleNamespace(path=source),
            ), patch(
                "pipeline.services.miolo_transform.publish_miolo_for_kdp",
                return_value=published_path,
            ):
                first = miolo_transform.ensure_md_uptodate(edition, cached_source_sha256=None)
                source.write_text("CHAPTER 1\nChanged line.\n", encoding="utf-8")
                second = miolo_transform.ensure_md_uptodate(
                    edition,
                    cached_source_sha256=str(first["source_sha256"]),
                )

            self.assertEqual(first["md_action"], "generated")
            self.assertEqual(second["md_action"], "generated")
            self.assertNotEqual(first["source_sha256"], second["source_sha256"])


class TxtToMdBehaviorTests(SimpleTestCase):
    def test_txt_to_md_marks_headings_only(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            src = root / "source.txt"
            out = root / "out.md"
            src.write_text(
                "CHAPTER 1 - Dawn\n\nBody line A.\n\nCHAPTER 2 - Night\nBody line B.\n",
                encoding="utf-8",
            )

            miolo_transform.txt_to_md(
                source=src,
                output=out,
                chapter_pattern=r"^CHAPTER\\s+\\d+.*$",
                lang="en",
            )

            md = out.read_text(encoding="utf-8")
            self.assertIn("# CHAPTER 1 - Dawn", md)
            self.assertIn("Body line A.", md)
            self.assertIn("# CHAPTER 2 - Night", md)
            self.assertIn("Body line B.", md)
            self.assertNotIn("\\newpage", md)


class TextSourceFallbackTests(SimpleTestCase):
    def test_resolve_txt_source_accepts_loose_build_filename(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            build_dir = data_root / "builds" / "book_0004" / "en"
            build_dir.mkdir(parents=True, exist_ok=True)
            loose = build_dir / "Conan_The_hour_of_the_Dragon.txt"
            loose.write_text("CHAPTER 1\nText\n", encoding="utf-8")
            edition = SimpleNamespace(
                language=SimpleNamespace(code="en"),
                work=SimpleNamespace(code="book_0004"),
            )

            with patch("pipeline.services.paths.data_dir", return_value=data_root), patch(
                "pipeline.services.paths.edition_build_dir", return_value=build_dir
            ):
                selected = text_source.resolve_txt_source(edition)

            self.assertEqual(selected.path, loose)
            self.assertIn("build fallback", selected.label)

    def test_resolve_txt_source_materializes_from_chunks_when_missing_txt(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            build_dir = data_root / "builds" / "book_0004" / "en"
            chunks_dir = data_root / "chunks" / "book_0004" / "en"
            build_dir.mkdir(parents=True, exist_ok=True)
            chunks_dir.mkdir(parents=True, exist_ok=True)

            (chunks_dir / "ch_001_chunk_001.txt").write_text("CHAPTER 1\nPart A\n", encoding="utf-8")
            (chunks_dir / "ch_001_chunk_002.txt").write_text("Part B\n", encoding="utf-8")
            manifest = {
                "schema_version": "chunks_manifest_v2",
                "chapters": [
                    {
                        "chunks": [
                            {"file_path": "ch_001_chunk_001.txt"},
                            {"file_path": "ch_001_chunk_002.txt"},
                        ]
                    }
                ],
            }
            (chunks_dir / "chunks_manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            edition = SimpleNamespace(
                language=SimpleNamespace(code="en"),
                work=SimpleNamespace(code="book_0004"),
            )

            with patch("pipeline.services.paths.data_dir", return_value=data_root), patch(
                "pipeline.services.paths.edition_build_dir", return_value=build_dir
            ):
                selected = text_source.resolve_txt_source(edition)

            self.assertEqual(selected.path.name, "source_from_chunks_en.txt")
            self.assertTrue(selected.path.exists())
            assembled = selected.path.read_text(encoding="utf-8")
            self.assertIn("CHAPTER 1", assembled)
            self.assertIn("Part B", assembled)
            self.assertIn("chunks fallback", selected.label)

    def test_resolve_txt_source_injects_synthetic_chapters_from_image_count(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            build_dir = data_root / "builds" / "book_0004" / "en"
            chunks_dir = data_root / "chunks" / "book_0004" / "en"
            raw_images_dir = data_root / "images" / "book_0004" / "en" / "raw"
            build_dir.mkdir(parents=True, exist_ok=True)
            chunks_dir.mkdir(parents=True, exist_ok=True)
            raw_images_dir.mkdir(parents=True, exist_ok=True)

            for idx in range(1, 5):
                (chunks_dir / f"ch_001_chunk_{idx:03d}.txt").write_text(
                    f"Text block {idx}\n",
                    encoding="utf-8",
                )
            # 2 images => synthesize 2 chapter anchors.
            (raw_images_dir / "01.png").write_bytes(b"a")
            (raw_images_dir / "02.png").write_bytes(b"b")

            edition = SimpleNamespace(
                language=SimpleNamespace(code="en"),
                work=SimpleNamespace(code="book_0004"),
            )

            with patch("pipeline.services.paths.data_dir", return_value=data_root), patch(
                "pipeline.services.paths.edition_build_dir", return_value=build_dir
            ):
                selected = text_source.resolve_txt_source(edition)

            self.assertEqual(selected.path.name, "source_from_chunks_en.txt")
            assembled = selected.path.read_text(encoding="utf-8")
            self.assertIn("CHAPTER 01", assembled)
            self.assertIn("CHAPTER 02", assembled)
            self.assertIn("Text block 4", assembled)

    def test_synthetic_chapter_detection_ignores_pronoun_i_lines(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            build_dir = data_root / "builds" / "book_0004" / "en"
            chunks_dir = data_root / "chunks" / "book_0004" / "en"
            raw_images_dir = data_root / "images" / "book_0004" / "en" / "raw"
            build_dir.mkdir(parents=True, exist_ok=True)
            chunks_dir.mkdir(parents=True, exist_ok=True)
            raw_images_dir.mkdir(parents=True, exist_ok=True)

            (chunks_dir / "ch_001_chunk_001.txt").write_text("I am Conan.\n", encoding="utf-8")
            (chunks_dir / "ch_001_chunk_002.txt").write_text("I know this road.\n", encoding="utf-8")
            (raw_images_dir / "01.png").write_bytes(b"a")
            (raw_images_dir / "02.png").write_bytes(b"b")

            edition = SimpleNamespace(
                language=SimpleNamespace(code="en"),
                work=SimpleNamespace(code="book_0004"),
            )

            with patch("pipeline.services.paths.data_dir", return_value=data_root), patch(
                "pipeline.services.paths.edition_build_dir", return_value=build_dir
            ):
                selected = text_source.resolve_txt_source(edition)

            assembled = selected.path.read_text(encoding="utf-8")
            self.assertIn("CHAPTER 01", assembled)
            self.assertIn("CHAPTER 02", assembled)
