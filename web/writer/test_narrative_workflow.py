from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase

from gaiden.writer_engine.narrative import merge_scenes
from web.writer.models import Chapter, ChapterSession, StoryProject
from web.writer.services.narrative import complete_chapter_workflow


class NarrativeMergeTests(TestCase):
    def setUp(self):
        self.project = StoryProject.objects.create(title="Mystery", language="English")
        self.chapter = Chapter.objects.create(
            project=self.project,
            number=1,
            title="The Telegram",
            session_count=2,
            target_words=1000,
            status=Chapter.Status.GENERATION_COMPLETE,
            session_scenes=[["Opening"], ["Clue"]],
        )
        for number, text in enumerate(("Validated scene one.", "Validated scene two."), start=1):
            ChapterSession.objects.create(
                chapter=self.chapter,
                number=number,
                status=ChapterSession.Status.COMPLETE,
                content=text,
                generation_parameters={
                    "qa_version": 1,
                    "qa_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "validation": {"passed": True, "scope": "mechanical_and_local_critic"},
                },
            )

    def test_merge_returns_one_complete_draft(self):
        generator = SimpleNamespace(model="local-model", last_finish_reason="stop")
        generator.generate = Mock(return_value="One continuous chapter.")
        result = merge_scenes(
            generator,
            language="English",
            chapter_number=1,
            chapter_title="The Telegram",
            scene_texts=["Scene one.", "Scene two."],
            target_words=500,
        )
        self.assertEqual(result, "One continuous chapter.")
        self.assertIn("Scene two.", generator.generate.call_args.kwargs["user"])

    def test_workflow_merges_once_and_preserves_the_canonical_scene_text(self):
        generator = SimpleNamespace(model="local-model", last_finish_reason="stop")
        generator.generate = Mock(return_value="Merged chapter.")
        with patch("web.writer.services.narrative.writer_generator", return_value=generator):
            complete_chapter_workflow(self.chapter)
            complete_chapter_workflow(self.chapter)
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.status, Chapter.Status.MERGED)
        self.assertEqual(self.chapter.merged_text, "Merged chapter.")
        self.assertEqual(self.chapter.merge_model, "local-model")
        generator.generate.assert_called_once()
