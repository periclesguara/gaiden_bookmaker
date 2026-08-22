from __future__ import annotations

from django.test import SimpleTestCase

from writer_engine.corpus import SourceChunk
from writer_engine.engine import NonfictionRequest, WriterEngine
from writer_engine.index import VectorIndex
from writer_engine.language_contract import default_language_contract


class FakeEmbedder:
    model = "test/embedding"

    def embed(self, texts):
        return [[1.0, 1.0] for _text in texts]


class FakeGenerator:
    model = "test/qwen"

    def __init__(self, text):
        self.text = text
        self.calls = []

    def generate(self, *, system, user, max_tokens):
        self.calls.append((system, user, max_tokens))
        return self.text


class NonfictionWriterEngineTests(SimpleTestCase):
    def _engine(self, generated_text):
        chunk = SourceChunk(
            chunk_id="chunk-1",
            source_path="economics/source.txt",
            source_sha256="a" * 64,
            ordinal=1,
            heading="Institutions",
            text="Institutions shape incentives and constrain political choices over time.",
            text_sha256="b" * 64,
        )
        generator = FakeGenerator(generated_text)
        index = VectorIndex(
            model=FakeEmbedder.model,
            dimension=2,
            rows=[(chunk, [2 ** -0.5, 2 ** -0.5])],
        )
        return WriterEngine(index=index, embedder=FakeEmbedder(), generator=generator), generator

    def _request(self):
        contract = default_language_contract()
        contract["validation"]["max_word_variation_percent"] = 100
        return NonfictionRequest(
            title="Institutions and development",
            language="en-US",
            direction="Preserve the operator thesis and explain its institutional mechanism.",
            operator_text="My argument is that durable rules alter incentives.",
            source_guidance="Use the approved institutional economics sources.",
            continuity="",
            language_contract=contract,
            target_words=400,
            citation_prefix="s1",
        )

    def test_develops_operator_text_and_renders_only_retrieved_sources(self):
        draft = (
            "Durable rules shape incentives by changing the expected costs and benefits "
            "that participants face across repeated political and economic decisions. "
            "[SRC:chunk-1]"
        )
        engine, generator = self._engine(draft)

        result = engine.create_nonfiction_chapter(self._request())

        self.assertIn("[^s1-1]", result.text)
        self.assertIn("## Sources for this session", result.text)
        self.assertIn(
            "[^s1-1]: economics/source.txt — Institutions — trecho chunk-1",
            result.text,
        )
        self.assertNotIn("[SRC:", result.text)
        self.assertEqual(result.source_chunk_ids, ("chunk-1",))
        system, user, _max_tokens = generator.calls[0]
        self.assertIn("operator's thesis", system)
        self.assertIn("Do not invent facts", system)
        self.assertIn("My argument is that durable rules alter incentives.", user)
        self.assertIn("chunk=chunk-1", user)

    def test_unknown_source_id_is_rejected(self):
        draft = (
            "This paragraph makes a factual claim with enough words to require a "
            "verifiable retrieved source at its end. [SRC:not-retrieved]"
        )
        engine, _generator = self._engine(draft)

        with self.assertRaisesMessage(ValueError, "unknown source IDs"):
            engine.create_nonfiction_chapter(self._request())

    def test_substantive_paragraph_must_end_with_source_marker(self):
        draft = (
            "This paragraph contains a source marker [SRC:chunk-1] but then continues "
            "with an unsupported factual ending that is not followed by a source."
        )
        engine, _generator = self._engine(draft)

        with self.assertRaisesMessage(ValueError, "must end with a source marker"):
            engine.create_nonfiction_chapter(self._request())
