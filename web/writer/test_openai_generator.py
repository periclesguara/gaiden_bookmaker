from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from gaiden.writer_engine.openai_generator import OpenAIWriterGenerator, writer_generator


class OpenAIWriterTests(TestCase):
    @patch("gaiden.writer_engine.openai_generator.OpenAI")
    def test_luna_request_is_bounded_and_has_no_qwen_parameters(self, client):
        client.return_value.responses.create.return_value = SimpleNamespace(
            status="completed", output_text="  Draft prose.  "
        )
        generator = OpenAIWriterGenerator(api_key="nonfunctional-test-placeholder")
        self.assertEqual(generator.generate(system="rules", user="brief", max_tokens=512), "Draft prose.")
        client.assert_called_once_with(api_key="nonfunctional-test-placeholder", base_url="https://api.openai.com/v1", max_retries=0, timeout=180)
        client.return_value.responses.create.assert_called_once_with(
            model="gpt-5.6-luna", instructions="rules", input="brief",
            max_output_tokens=512, reasoning={"effort": "none"}, store=False,
        )
        self.assertEqual(generator.last_finish_reason, "stop")

    @patch("gaiden.writer_engine.openai_generator.OpenAI")
    def test_empty_incomplete_and_refused_outputs_fail_closed(self, client):
        generator = OpenAIWriterGenerator(api_key="nonfunctional-test-placeholder")
        for status, content in [("incomplete", "partial"), ("failed", "partial"), ("completed", "")]:
            with self.subTest(status=status, content=content):
                client.return_value.responses.create.return_value = SimpleNamespace(status=status, output_text=content)
                with self.assertRaisesRegex(RuntimeError, "empty or incomplete"):
                    generator.generate(system="rules", user="brief", max_tokens=512)

    @patch("gaiden.writer_engine.openai_generator.OpenAI")
    def test_token_limits_are_checked_before_request(self, client):
        generator = OpenAIWriterGenerator(api_key="nonfunctional-test-placeholder")
        for count in (0, 255, 32769):
            with self.assertRaises(ValueError):
                generator.generate(system="rules", user="brief", max_tokens=count)
        client.return_value.responses.create.assert_not_called()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "nonfunctional-test-placeholder", "OPENAI_BASE_URL": "https://untrusted.invalid", "GAIDEN_QWEN_MODEL": "old-qwen"}, clear=True)
    @patch("gaiden.writer_engine.openai_generator.get_openai_api_key")
    @patch("gaiden.writer_engine.openai_generator.OpenAI")
    def test_reuses_environment_key_and_isolates_writer_routing(self, client, config):
        self.assertEqual(writer_generator().model, "gpt-5.6-luna")
        config.assert_not_called()
        self.assertEqual(client.call_args.kwargs["base_url"], "https://api.openai.com/v1")

    @patch.dict("os.environ", {}, clear=True)
    @patch("gaiden.writer_engine.openai_generator.get_openai_api_key", return_value="nonfunctional-test-placeholder")
    @patch("gaiden.writer_engine.openai_generator.OpenAI")
    def test_reuses_registered_system_key(self, client, config):
        writer_generator()
        self.assertEqual(client.call_args.kwargs["api_key"], "nonfunctional-test-placeholder")

    @patch.dict("os.environ", {}, clear=True)
    @patch("gaiden.writer_engine.openai_generator.get_openai_api_key", return_value="")
    @patch("gaiden.writer_engine.openai_generator.OpenAI")
    def test_missing_key_stops_before_client_creation(self, client, config):
        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            writer_generator()
        client.assert_not_called()

    @patch("gaiden.writer_engine.openai_generator.OpenAI")
    def test_transport_error_has_no_retry_or_sensitive_details(self, client):
        client.return_value.responses.create.side_effect = RuntimeError("sensitive request contents")
        generator = OpenAIWriterGenerator(api_key="nonfunctional-test-placeholder")
        with self.assertRaisesRegex(RuntimeError, "OpenAI Writer request failed") as caught:
            generator.generate(system="rules", user="brief", max_tokens=512)
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertEqual(client.return_value.responses.create.call_count, 1)


class WriterRoutingTests(TestCase):
    @patch("gaiden.writer_engine.cli.writer_generator")
    def test_cli_uses_writer_factory(self, factory):
        from gaiden.writer_engine.cli import _generator
        self.assertIs(_generator(), factory.return_value)

    def test_django_uses_luna_without_local_generation(self):
        from importlib import import_module
        from unittest.mock import MagicMock
        generation = import_module(f"{__package__}.services.generation")
        chapter = MagicMock()
        chapter.project.vector_index_path = "/unused/index.jsonl"
        chapter.project.writing_mode = "FICTION"
        with patch.object(generation, "writer_generator") as factory, patch.object(
            generation, "OpenAIEmbeddingClient"
        ), patch.object(generation.VectorIndex, "load"), patch(
            "urllib.request.urlopen", side_effect=AssertionError("unexpected local generation")
        ):
            engine = generation._engine(chapter)
        self.assertIs(engine.generator, factory.return_value)
        factory.assert_called_once_with()
