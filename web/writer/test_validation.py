from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from gaiden.writer_engine.engine import GenerationResult
from gaiden.writer_engine.repair import (CHECKS, STATE_FIELDS, LIST_FIELDS, ManualReviewRequired,
    generate_validated_scene, review_scene)
from gaiden.writer_engine.validation import (SceneContract, validate_scene, validate_completion,
    split_sentences, detect_language, BANNED_MODERN_TERMS, ALLOWED_FRENCH_TERMS)
from web.writer.models import Chapter

TEXT = ('I followed Holmes into the drawing room and placed my hat upon a chair. '
    'The window stood open, although the evening was bitterly cold. '
    'A torn envelope lay beside the lamp. My companion examined its seal without speaking. '
    'At last he asked the housekeeper who had delivered the letter. '
    'She pointed towards the narrow passage behind us.')
CONTRACT = SceneContract(purpose='An investigation in a drawing room.', min_words=20, max_words=300,
    pov='FIRST_PERSON_WATSON', expected_language='en')


def critic_payload(status='pass', **overrides):
    payload = {'checks':{key:{'status':status, 'reason':'The scene follows the contract.', 'evidence':''} for key in CHECKS},
        'continuity':{key:[] if key in LIST_FIELDS else '' for key in STATE_FIELDS},
        'continuity_evidence':{}}
    payload.update(overrides)
    return payload


class MechanicalValidationTests(SimpleTestCase):
    def codes(self, text, contract=CONTRACT, **kwargs):
        return {i.code for i in validate_scene(text, contract, **kwargs).issues}

    def test_natural_english_scene_passes_mechanical_checks(self):
        self.assertTrue(validate_scene(TEXT, CONTRACT).passed)

    def test_portuguese_output_inside_english_project_rejected(self):
        text = 'Holmes levantou-se lentamente e caminhou até a janela. Os banqueiros observavam em silêncio. Eu coloquei o chapéu sobre a mesa e esperei que ele começasse a falar.'
        self.assertIn('LANGUAGE_MISMATCH', self.codes(text))

    def test_mixed_language_segment_rejected(self):
        text = TEXT+'\n\nOs banqueiros estavam preocupados com a situação. Eu sabia que não poderíamos sair daquela sala antes de compreender tudo o que havia acontecido naquela manhã.'
        self.assertTrue({'LANGUAGE_MISMATCH','EXCESSIVE_LANGUAGE_MIXING'} & self.codes(text))

    def test_allowed_french_names_do_not_change_language(self):
        contract = replace(CONTRACT, allowed_foreign_terms=ALLOWED_FRENCH_TERMS)
        self.assertNotIn('LANGUAGE_MISMATCH',self.codes(TEXT+' We left for the Grand Palais with Monsieur Dupont.',contract))

    def test_abbreviations_quotes_and_short_final_sentence_are_not_truncation(self):
        self.assertEqual(len(split_sentences('Dr. Watson entered. Holmes waited.')),2)
        self.assertEqual(validate_completion('“Come, Watson.”'),[])
        self.assertEqual(validate_completion('"Come, Watson."'),[])
        self.assertEqual(validate_completion('“We must examine the letter.\n\n“Only then shall we leave.”'),[])

    def test_punctuated_token_limit_output_is_still_rejected(self):
        self.assertIn('TRUNCATED_RESPONSE',self.codes(TEXT,finish_reason='length'))

    def test_actual_failure_categories_truncation_and_open_dialogue(self):
        self.assertIn('INCOMPLETE_ENDING',self.codes('Holmes turned towards the banker and'))
        self.assertIn('UNCLOSED_QUOTE',self.codes('“We must examine the letter.'))

    def test_runaway_chain_is_rejected(self):
        codes=self.codes('I observed the machine '+ ' '.join('technology' for _ in range(393)))
        self.assertIn('RUNAWAY_SENTENCE',codes)
        self.assertIn('DEGENERATE_GENERATION',codes)

    def test_internal_and_previous_repetition_are_rejected(self):
        self.assertIn('INTERNAL_REPETITION',self.codes((TEXT+' ')*4,replace(CONTRACT,max_words=1000)))
        self.assertIn('EXACT_SESSION_REPETITION',self.codes(TEXT,previous_text=TEXT))

    def test_corpus_copy_gate_is_retained(self):
        self.assertIn('SOURCE_COPY',self.codes(TEXT,sources=[TEXT]))

    def test_contract_length_is_enforced(self):
        self.assertIn('SCENE_TOO_SHORT',self.codes(TEXT,replace(CONTRACT,min_words=700,max_words=1300)))

    def test_modern_term_is_editorial_gate_and_word_boundary_is_respected(self):
        contract=replace(CONTRACT,banned_terms=BANNED_MODERN_TERMS)
        self.assertIn('PROHIBITED_VOCABULARY',self.codes(TEXT+' He described information theory.',contract))
        self.assertNotIn('PROHIBITED_VOCABULARY',self.codes(TEXT+' A digitalis plant stood nearby.',contract))

    def test_holmes_private_thoughts_are_flagged_for_critic(self):
        self.assertIn('POV_VIOLATION',self.codes(TEXT+' Holmes secretly understood everything.'))

    def test_language_detector_is_deterministic(self):
        self.assertEqual(detect_language(TEXT),detect_language(TEXT))


class GenerationTokenBudgetTests(SimpleTestCase):
    def test_literary_scene_has_headroom_to_finish_naturally(self):
        from gaiden.writer_engine.engine import ChapterRequest, WriterEngine

        engine = object.__new__(WriterEngine)
        engine.index = Mock()
        engine.embedder = Mock()
        engine.generator = Mock()
        hit = SimpleNamespace(
            chunk=SimpleNamespace(text="reference", chunk_id="chunk-1"),
            score=0.9,
        )
        with patch(
            "gaiden.writer_engine.engine.retrieve",
            return_value=SimpleNamespace(context="reference", hits=[hit]),
        ) as retrieval:
            prepared = engine.prepare_chapter(ChapterRequest(
                title="Chapter 01 — session 02",
                language="English",
                brief="Visit the Grand Palais.",
                continuity="Watson narrates.",
                point_of_view="First-person Watson",
                target_words=1300,
            ))
        self.assertEqual(prepared.max_tokens, 4096)
        self.assertIn("LANGUAGE LOCK: Write the narrative only in English", prepared.system)
        self.assertIn("FINAL LANGUAGE REMINDER", prepared.user)
        self.assertEqual(retrieval.call_args.kwargs["max_context_chars"], 12000)


class RepairPipelineTests(SimpleTestCase):
    def setUp(self):
        self.directory=TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)/'session 01'
        self.engine=Mock()
        self.engine.prepare_chapter.return_value=SimpleNamespace(user='Original operator prompt',sources=())
        self.engine.generator.last_finish_reason='stop'
        self.engine.generator.generate.return_value=json.dumps(critic_payload())

    def run_scene(self, texts, contract=CONTRACT):
        self.engine.generate_candidate.side_effect=[GenerationResult(t,'test/local',(),()) if isinstance(t,str) else t for t in texts]
        return generate_validated_scene(self.engine,object(),contract,session_root=self.root)

    def test_bad_then_good_preserves_attempts_lowers_temperature_and_stops(self):
        scene=self.run_scene(['Texto errado em português e sem final',TEXT])
        calls=self.engine.generate_candidate.call_args_list
        self.assertEqual(len(calls),2)
        self.assertEqual([c.kwargs['temperature'] for c in calls],[.75,.60])
        self.assertIn('Original operator prompt',calls[1].kwargs['prompt'])
        self.assertIn('LANGUAGE_MISMATCH',calls[1].kwargs['prompt'])
        self.assertEqual(self.engine.prepare_chapter.call_count,1)
        self.assertEqual(self.engine.generator.generate.call_count,1)
        path=Path(scene.audit_path)
        self.assertEqual((path/'attempts/attempt_01.txt').read_text(),'Texto errado em português e sem final')
        self.assertEqual((path/'approved.txt').read_text(),TEXT)
        self.assertEqual(json.loads((path/'validation.json').read_text())['editorial_status'],'DRAFT')
        self.assertTrue((path/'continuity.json').exists())

    def test_three_total_attempts_then_manual_review_without_approved_file(self):
        with self.assertRaisesMessage(ManualReviewRequired,'after 3 attempts'):
            self.run_scene(['fragment']*3)
        self.assertEqual(self.engine.generate_candidate.call_count,3)
        self.engine.generator.generate.assert_not_called()
        self.assertEqual(len(list(self.root.rglob('attempt_*.txt'))),3)
        self.assertEqual(len(list(self.root.rglob('manual-review.json'))),1)
        self.assertEqual(list(self.root.rglob('approved.txt')),[])

    def test_transport_error_is_not_a_repair_attempt(self):
        with self.assertRaises(TimeoutError):
            self.run_scene([TimeoutError('offline')])
        self.assertEqual(self.engine.generate_candidate.call_count,1)
        self.assertEqual(list(self.root.rglob('approved.txt')),[])

    def test_critic_failure_is_fail_closed_with_original_candidate_preserved(self):
        self.engine.generator.generate.return_value='invalid JSON'
        with self.assertRaises(ManualReviewRequired):
            self.run_scene([TEXT])
        self.assertEqual(self.engine.generate_candidate.call_count,1)
        self.assertEqual(len(list(self.root.rglob('attempt_*.txt'))),1)
        self.assertEqual(list(self.root.rglob('approved.txt')),[])

    def test_fenced_json_from_local_model_is_accepted(self):
        self.engine.generator.generate.return_value = (
            '```json\n' + json.dumps(critic_payload()) + '\n```'
        )
        scene = self.run_scene([TEXT])
        self.assertTrue(scene.validation.passed)

    def test_ungrounded_continuity_is_discarded_without_approving_new_facts(self):
        payload = critic_payload()
        payload['continuity']['location'] = 'invented place'
        payload['continuity_evidence'] = {'invented place': 'not in the candidate'}
        self.engine.generator.generate.return_value = json.dumps(payload)
        scene = self.run_scene([TEXT])
        self.assertEqual(scene.continuity['location'], '')

    def test_semantic_failure_is_repaired_not_approved(self):
        payload=critic_payload()
        payload['checks']['scene_contract']['status']='fail'
        payload['checks']['scene_contract']['reason']='The banker meeting is missing.'
        self.engine.generator.generate.return_value=json.dumps(payload)
        with self.assertRaises(ManualReviewRequired):
            self.run_scene([TEXT]*3)
        self.assertEqual(self.engine.generator.generate.call_count,3)
        self.assertEqual(list(self.root.rglob('approved.txt')),[])

    def test_uncertain_critic_stops_without_rewriting(self):
        self.engine.generator.generate.return_value=json.dumps(critic_payload('uncertain'))
        with self.assertRaises(ManualReviewRequired):
            self.run_scene([TEXT])
        self.assertEqual(self.engine.generate_candidate.call_count,1)

    def test_ungrounded_continuity_never_enters_saved_state(self):
        payload=critic_payload()
        payload['continuity']['location']='drawing room'
        payload['continuity_evidence']={'drawing room':'unwritten claim'}
        self.engine.generator.generate.return_value=json.dumps(payload)
        scene = self.run_scene([TEXT])
        self.assertEqual(scene.continuity['location'], '')

    def test_grounded_continuity_is_saved(self):
        payload=critic_payload()
        payload['continuity']['location']='drawing room'
        payload['continuity_evidence']={'drawing room':'I followed Holmes into the drawing room'}
        self.engine.generator.generate.return_value=json.dumps(payload)
        scene=self.run_scene([TEXT])
        self.assertEqual(scene.continuity['location'],'drawing room')

    def test_no_shared_keyword_event_approval(self):
        contract=replace(CONTRACT,required_events=['Holmes solves the main mystery'])
        payload=critic_payload()
        payload['checks']['required_event_1']={
            'status':'fail',
            'reason':'The event is absent; shared names do not establish it.',
            'evidence':'',
        }
        self.engine.generator.generate.return_value=json.dumps(payload)
        with self.assertRaises(ManualReviewRequired):
            self.run_scene([TEXT]*3,contract)
        self.assertEqual(list(self.root.rglob('approved.txt')),[])

    def test_required_event_pass_without_candidate_evidence_is_invalid(self):
        contract=replace(CONTRACT,required_events=['Holmes examines an envelope'])
        payload=critic_payload()
        payload['checks']['required_event_1']={
            'status':'pass',
            'reason':'The event supposedly occurs.',
            'evidence':'',
        }
        self.engine.generator.generate.return_value=json.dumps(payload)
        with self.assertRaisesMessage(ManualReviewRequired,'Invalid local critic'):
            self.run_scene([TEXT],contract)
        self.assertEqual(list(self.root.rglob('approved.txt')),[])

    def test_separate_authorized_runs_never_overwrite_prior_attempts(self):
        first=self.run_scene([TEXT])
        second=self.run_scene([TEXT])
        self.assertNotEqual(first.audit_path,second.audit_path)
        self.assertEqual((Path(first.audit_path)/'approved.txt').read_text(),TEXT)

    def test_existing_text_is_preserved_in_same_repair_audit(self):
        self.engine.generate_candidate.side_effect=[GenerationResult(TEXT,'test/local',(),())]
        scene=generate_validated_scene(
            self.engine,
            object(),
            CONTRACT,
            session_root=self.root,
            original_text='Legacy text that must remain recoverable.',
        )
        self.assertEqual(
            (Path(scene.audit_path)/'original.txt').read_text(),
            'Legacy text that must remain recoverable.',
        )

    def test_explicit_local_critic_is_used_instead_of_the_author(self):
        critic = Mock()
        critic.last_finish_reason = 'stop'
        critic.generate.return_value = json.dumps(critic_payload())
        self.engine.generate_candidate.side_effect = [GenerationResult(TEXT, 'test/local', (), ())]

        generate_validated_scene(
            self.engine, object(), CONTRACT, session_root=self.root,
            critic_generator=critic,
        )

        critic.generate.assert_called_once()
        self.engine.generator.generate.assert_not_called()


class CriticRoutingTests(SimpleTestCase):
    @patch.dict('os.environ', {'GAIDEN_WRITER_CRITIC_PROVIDER': 'openai'})
    @patch('web.writer.services.validation.writer_generator')
    def test_legacy_provider_setting_cannot_route_away_from_local_writer(self, factory):
        from web.writer.services.validation import critic_generator
        self.assertIs(critic_generator(), factory.return_value)
        factory.assert_called_once_with()

class StoredApprovalTests(TestCase):
    def setUp(self):
        from web.writer.models import Chapter, ChapterSession, StoryProject
        self.chapter=Chapter.objects.create(project=StoryProject.objects.create(title='QA'),
            number=1,session_count=1,status=Chapter.Status.GENERATION_COMPLETE)
        self.session=ChapterSession.objects.create(chapter=self.chapter,number=1,
            status=ChapterSession.Status.COMPLETE,content=TEXT)

    def test_legacy_complete_status_is_not_qa_approval(self):
        self.assertFalse(self.chapter.qa_ready)
        with self.assertRaisesMessage(ValueError,'validação'):
            self.chapter.finalize()
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.final_text,'')

    def test_approval_is_bound_to_exact_text(self):
        import hashlib
        self.session.generation_parameters={'qa_version':1,
            'qa_sha256':hashlib.sha256(TEXT.encode()).hexdigest(),
            'validation':{'passed':True,'scope':'mechanical_and_local_critic'}}
        self.session.save()
        self.assertTrue(self.chapter.qa_ready)
        self.session.content=TEXT+' I closed the door.'
        self.session.save()
        self.assertFalse(self.chapter.qa_ready)
        with self.assertRaises(ValueError):
            self.chapter.finalize()

    def test_interface_marks_unvalidated_legacy_session(self):
        from django.urls import reverse
        response=self.client.get(reverse('writer:chapter_detail',args=[self.chapter.pk]))
        self.assertContains(response,'sessão sem aprovação da nova validação')
        self.assertContains(response,'disabled')


class LocalEndpointTests(SimpleTestCase):
    def test_writer_rejects_remote_model_endpoint(self):
        from gaiden.infrastructure.local_models import _local_base_url
        from unittest.mock import patch
        with patch.dict("os.environ", {"GAIDEN_LOCAL_MODEL_URL": "https://api.example.test/v1"}):
            with self.assertRaisesMessage(ValueError, "loopback"):
                _local_base_url()


class ExistingSessionRepairTests(TestCase):
    def setUp(self):
        from web.writer.models import Chapter, ChapterSession, StoryProject

        project = StoryProject.objects.create(
            title="The Devil in Paris",
            language="English (United Kingdom)",
        )
        self.chapter = Chapter.objects.create(
            project=project,
            number=1,
            session_count=4,
            status=Chapter.Status.GENERATION_COMPLETE,
            session_scenes=[["one"], ["two"], ["three"], ["four"]],
        )
        for number in range(1, 5):
            ChapterSession.objects.create(
                chapter=self.chapter,
                number=number,
                status=ChapterSession.Status.COMPLETE,
                content=f"Original session {number}.",
            )

    @patch("web.writer.services.repair.generate_validated_session")
    @patch("web.writer.services.repair.build_session_request")
    @patch("web.writer.services.repair._engine")
    def test_django_repairs_only_selected_sessions_and_preserves_origin_hash(
        self, engine_factory, request_builder, validator
    ):
        from gaiden.writer_engine.repair import ValidatedScene
        from gaiden.writer_engine.validation import ValidationResult
        from web.writer.services.repair import repair_chapter_sessions

        request_builder.side_effect = lambda chapter, number, scenes: (
            f"previous {number}",
            object(),
            object(),
        )

        def validated(chapter, number, engine, request, contract, previous, **kwargs):
            return ValidatedScene(
                GenerationResult(f"Repaired session {number}.", "local/model", (), ()),
                ValidationResult(True, 100, scope="mechanical_and_local_critic"),
                {"location": f"location {number}"},
                f"/audit/{number}",
            )

        validator.side_effect = validated
        repair_chapter_sessions(self.chapter, [4, 2, 3, 3])

        sessions = list(self.chapter.sessions.order_by("number"))
        self.assertEqual(sessions[0].content, "Original session 1.")
        self.assertEqual(
            [session.content for session in sessions[1:]],
            ["Repaired session 2.", "Repaired session 3.", "Repaired session 4."],
        )
        self.assertEqual(validator.call_count, 3)
        self.assertEqual(
            [call.kwargs["original_text"] for call in validator.call_args_list],
            ["Original session 2.", "Original session 3.", "Original session 4."],
        )
        self.assertTrue(sessions[1].generation_parameters["repair"])
        self.assertEqual(len(sessions[1].generation_parameters["repaired_from_sha256"]), 64)
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.status, Chapter.Status.GENERATION_COMPLETE)
        engine_factory.assert_called_once()

    @patch("web.writer.services.repair.generate_validated_session")
    @patch("web.writer.services.repair.build_session_request")
    @patch("web.writer.services.repair._engine")
    def test_failed_repair_preserves_session_and_stops_sequence(
        self, engine_factory, request_builder, validator
    ):
        from gaiden.writer_engine.repair import ManualReviewRequired
        from web.writer.services.repair import repair_chapter_sessions

        request_builder.return_value = ("previous", object(), object())
        validator.side_effect = ManualReviewRequired("manual review")
        with self.assertRaises(ManualReviewRequired):
            repair_chapter_sessions(self.chapter, [2, 3, 4])
        self.assertEqual(validator.call_count, 1)
        self.assertEqual(
            self.chapter.sessions.get(number=2).content,
            "Original session 2.",
        )
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.status, Chapter.Status.FAILED)

    @patch("web.writer.services.repair._engine")
    def test_repair_engine_setup_failure_releases_chapter_claim(self, engine_factory):
        from web.writer.services.repair import repair_chapter_sessions

        engine_factory.side_effect = RuntimeError("offline")
        with self.assertRaisesMessage(RuntimeError, "offline"):
            repair_chapter_sessions(self.chapter, [2])
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.status, Chapter.Status.FAILED)

    def test_repair_rejects_missing_or_final_sessions_before_model_setup(self):
        from web.writer.services.repair import repair_chapter_sessions

        with self.assertRaises(ValueError):
            repair_chapter_sessions(self.chapter, [5])
        self.chapter.status = Chapter.Status.FINAL
        self.chapter.save(update_fields=("status",))
        with self.assertRaisesMessage(ValueError, "finalizado"):
            repair_chapter_sessions(self.chapter, [2])
