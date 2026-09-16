from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse

from gaiden.writer_engine.narrative import (
    audit_narrative_logic,
    merge_scenes,
    update_narrative_state,
)
from gaiden.writer_engine.clients import LocalChatGenerator
from web.writer.models import Chapter, ChapterSession, StoryProject
from web.writer.services.generation import build_session_request
from web.writer.services.dashboard import build_project_dashboard
from web.writer.services.narrative import complete_chapter_workflow, run_chapter_workflow


def state_payload(chapter: int, *, clue: str = "sealed telegram") -> dict:
    return {
        "schema_version": 1,
        "chapter": chapter,
        "events": ["Holmes examined the telegram."],
        "character_state": {
            "Holmes": {"location": "London", "status": ["investigating"], "knows": [clue]}
        },
        "timeline": ["Evening: the telegram was examined."],
        "open_threads": ["Who forged the telegram?"],
        "resolved_threads": [],
        "new_clues": [clue],
        "important_objects": ["telegram"],
        "relationship_changes": [],
        "facts_to_remember": ["Watson saw the seal."],
        "consequences": ["The inquiry moves to the station."],
    }


def audit_payload(chapter: int, *, verdict: str = "PASS") -> dict:
    issues = [] if verdict == "PASS" else ["Travel time is not established."]
    return {
        "schema_version": 1,
        "chapter": chapter,
        "verdict": verdict,
        "causality": {"established": ["Telegram leads to station."], "issues": []},
        "temporality": {"established": ["Evening follows afternoon."], "issues": issues},
        "coherence": {"established": ["Watson knows only what he observed."], "issues": []},
        "micro_bridges": ["State the train departure time."] if issues else [],
    }


def generator(model: str, output) -> SimpleNamespace:
    instance = SimpleNamespace(model=model, last_finish_reason="stop")
    instance.generate = Mock(side_effect=output if isinstance(output, list) else None)
    if not isinstance(output, list):
        instance.generate.return_value = output
    return instance


class NarrativeCoreTests(TestCase):
    def test_local_memory_and_audit_clients_reject_placeholder_on_remote_endpoint(self):
        with self.assertRaisesMessage(ValueError, "real API key"):
            LocalChatGenerator(
                base_url="https://models.example.test/v1",
                api_key="placeholder",
                model="test-model",
            )

    def test_merge_returns_one_bounded_draft_and_rejects_incomplete_output(self):
        model = generator("merge-model", "One continuous chapter.")
        merged = merge_scenes(
            model,
            language="English",
            chapter_number=1,
            chapter_title="The Telegram",
            scene_texts=["Scene one.", "Scene two.", "Scene three.", "Scene four."],
            target_words=2500,
        )
        self.assertEqual(merged, "One continuous chapter.")
        self.assertIn('"ordered_scenes"', model.generate.call_args.kwargs["user"])
        self.assertIn("Scene four.", model.generate.call_args.kwargs["user"])
        model.last_finish_reason = "length"
        with self.assertRaisesMessage(ValueError, "incomplete"):
            merge_scenes(
                model, language="English", chapter_number=1, chapter_title="Title",
                scene_texts=["Scene."], target_words=400,
            )

    def test_qwen_memory_and_phi_audit_require_strict_structured_results(self):
        memory_model = generator("qwen-4b", json.dumps(state_payload(2)))
        memory = update_narrative_state(
            memory_model,
            chapter_number=2,
            previous_state=state_payload(1, clue="anonymous letter"),
            merged_chapter="Holmes examined the telegram.",
        )
        self.assertEqual(memory.value["chapter"], 2)
        self.assertEqual(memory.model, "qwen-4b")
        phi = generator("phi-mini", json.dumps(audit_payload(2)))
        audit = audit_narrative_logic(
            phi,
            chapter_number=2,
            previous_state=state_payload(1),
            current_state=memory.value,
            merged_chapter="Holmes examined the telegram.",
        )
        self.assertEqual(audit.value["verdict"], "PASS")

    def test_memory_discards_nontext_items_instead_of_recording_them_as_facts(self):
        payload = state_payload(2)
        payload["important_objects"] = ["telegram", {"name": "invented object"}]
        memory = update_narrative_state(
            generator("qwen-4b", json.dumps(payload)),
            chapter_number=2,
            previous_state={},
            merged_chapter="Holmes examined the telegram.",
        )
        self.assertEqual(memory.value["important_objects"], ["telegram"])

    def test_phi_pass_cannot_hide_reported_conflicts(self):
        payload = audit_payload(1)
        payload["coherence"]["issues"] = ["Watson knows an undisclosed fact."]
        with self.assertRaisesMessage(ValueError, "verdict conflicts"):
            audit_narrative_logic(
                generator("phi-mini", json.dumps(payload)),
                chapter_number=1,
                previous_state={},
                current_state=state_payload(1),
                merged_chapter="Draft.",
            )


class NarrativeWorkflowTests(TestCase):
    def setUp(self):
        self.project = StoryProject.objects.create(
            title="Mystery",
            language="English",
            character_bible="Holmes investigates.",
            supporting_characters_bible="Watson narrates.",
            antagonist_bible="The antagonist conceals evidence.",
            scenario_bible="Victorian London.",
            world_bible="1895.",
            story_direction="Follow the forged telegram.",
            story_outline="Investigation, reversal, resolution.",
            chapter_count=2,
        )
        self.chapter = Chapter.objects.create(
            project=self.project,
            number=1,
            title="The Telegram",
            session_count=4,
            target_words=2000,
            status=Chapter.Status.GENERATION_COMPLETE,
            session_scenes=[["Opening"], ["Clue"], ["Pursuit"], ["Consequence"]],
        )
        for number in range(1, 5):
            text = f"Validated scene {number}."
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

    def model_set(self):
        return (
            generator("luna", "Merged chapter."),
            generator("qwen-4b", json.dumps(state_payload(1))),
            generator("phi-mini", json.dumps(audit_payload(1))),
        )

    def test_complete_workflow_merges_once_then_updates_memory_and_audit(self):
        merger, memory, phi = self.model_set()
        with (
            patch("web.writer.services.narrative.writer_generator", return_value=merger),
            patch("web.writer.services.narrative.narrative_memory_generator", return_value=memory),
            patch("web.writer.services.narrative.logic_audit_generator", return_value=phi),
        ):
            complete_chapter_workflow(self.chapter)
            run_chapter_workflow(self.chapter)
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.status, Chapter.Status.MERGED)
        self.assertEqual(self.chapter.merged_text, "Merged chapter.")
        self.assertEqual(self.chapter.narrative_state["chapter"], 1)
        self.assertEqual(self.chapter.logic_audit["verdict"], "PASS")
        merger.generate.assert_called_once()
        memory.generate.assert_called_once()
        phi.generate.assert_called_once()
        self.chapter.finalize()
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.status, Chapter.Status.FINAL)
        self.assertEqual(self.chapter.final_text, "Merged chapter.")

    def test_failed_memory_keeps_merge_and_resume_does_not_regenerate_it(self):
        merger = generator("luna", "Preserved merge.")
        with (
            patch("web.writer.services.narrative.writer_generator", return_value=merger),
            patch("web.writer.services.narrative.narrative_memory_generator", return_value=generator("qwen", "not json")),
        ):
            with self.assertRaisesMessage(ValueError, "invalid JSON"):
                complete_chapter_workflow(self.chapter)
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.status, Chapter.Status.FAILED)
        self.assertEqual(self.chapter.merged_text, "Preserved merge.")
        memory = generator("qwen", json.dumps(state_payload(1)))
        phi = generator("phi", json.dumps(audit_payload(1)))
        with (
            patch("web.writer.services.narrative.writer_generator") as merger_factory,
            patch("web.writer.services.narrative.narrative_memory_generator", return_value=memory),
            patch("web.writer.services.narrative.logic_audit_generator", return_value=phi),
        ):
            complete_chapter_workflow(self.chapter)
        merger_factory.assert_not_called()
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.status, Chapter.Status.MERGED)

    def test_next_chapter_prompt_contains_cumulative_memory_and_audit(self):
        self.chapter.narrative_state = state_payload(1)
        self.chapter.logic_audit = audit_payload(1)
        self.chapter.status = Chapter.Status.MERGED
        self.chapter.save()
        following = Chapter.objects.create(
            project=self.project,
            number=2,
            session_count=4,
            session_scenes=[["A"], ["B"], ["C"], ["D"]],
        )
        _previous, _contract, request = build_session_request(
            following, 1, following.session_scenes
        )
        self.assertIn("Who forged the telegram?", request.continuity)
        self.assertIn("Telegram leads to station.", request.continuity)

    def test_project_generate_rejects_stale_confirmed_chapter(self):
        row = {"chapter": self.chapter}
        with (
            patch("web.writer.views.build_project_dashboard", return_value={"next_chapter_row": row}),
            patch("web.writer.views.run_chapter_workflow") as workflow,
        ):
            response = self.client.post(
                reverse("writer:generate_next", args=[self.project.pk]),
                {"confirm": "yes", "chapter_id": "999"},
            )
        self.assertEqual(response.status_code, 302)
        workflow.assert_not_called()

    def test_dashboard_selects_only_first_sequential_ready_chapter(self):
        from web.writer.models import SourceDocument

        source = SourceDocument.objects.create(
            filename="source.txt",
            source_path="/external/source.txt",
            normalized_path="/external/normalized.txt",
            normalized_sha256="a" * 64,
            status=SourceDocument.Status.VECTORIZED,
            vectorized_at=self.chapter.updated_at,
        )
        self.project.sources.add(source)
        second = Chapter.objects.create(
            project=self.project,
            number=2,
            session_count=4,
            session_scenes=[["A"], ["B"], ["C"], ["D"]],
        )
        third = Chapter.objects.create(
            project=self.project,
            number=3,
            session_count=4,
            session_scenes=[["E"], ["F"], ["G"], ["H"]],
        )
        self.chapter.status = Chapter.Status.MERGED
        self.chapter.merged_text = "Existing merge."
        self.chapter.save()
        project = StoryProject.objects.prefetch_related("sources", "chapters__sessions").get(
            pk=self.project.pk
        )
        with (
            patch("web.writer.services.dashboard._index_status", return_value={
                "valid": True, "reason": "Ready.", "chunk_count": 10, "dimension": 32,
                "embedding_model": "test", "source_count": 1,
            }),
            patch("web.writer.services.dashboard._model_status", return_value={
                "checked": False, "online": None, "embedding_model": "test",
                "writing_model": "luna", "narrative_memory_model": "qwen",
                "logic_audit_model": "phi",
            }),
        ):
            dashboard = build_project_dashboard(project)
        self.assertEqual(dashboard["next_chapter_row"]["chapter"].pk, second.pk)
        third_row = next(row for row in dashboard["chapter_rows"] if row["chapter"].pk == third.pk)
        self.assertFalse(third_row["generation_ready"])
        self.assertIn("Concluir o capítulo anterior primeiro", third_row["generation_blockers"])

    def test_project_page_has_one_generate_action_not_one_per_chapter(self):
        ready_row = {"chapter": self.chapter}
        dashboard = {
            "progress_percent": 0, "completed_stages": 0, "current_stage": None,
            "sources": {"normalized": 0, "selected": 0, "vectorized": 0,
                        "all_normalized": False, "all_vectorized": False, "failed": 0},
            "index": {"chunk_count": 0, "dimension": 0, "reason": "Pending", "valid": False},
            "chapters": {"prepared": 1, "total": 2, "generated": 0},
            "models": {"online": None, "checked": False, "reason": "Not checked",
                       "embedding_model": "embed", "writing_model": "luna",
                       "narrative_memory_model": "qwen", "logic_audit_model": "phi"},
            "stages": [], "global_generation_blockers": [], "vectorize_blockers": ["Pending"],
            "chapter_rows": [], "next_chapter_row": ready_row,
        }
        with patch("web.writer.views.build_project_dashboard", return_value=dashboard):
            response = self.client.get(reverse("writer:project_detail", args=[self.project.pk]))
        self.assertContains(response, reverse("writer:generate_next", args=[self.project.pk]), count=1)
        self.assertNotContains(response, "generate-dialog-")
