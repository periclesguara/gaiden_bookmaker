"""Local-first RAG and book-writing engine for Gaiden."""

from .engine import ChapterRequest, GenerationResult, WriterEngine
from .index import VectorIndex

__all__ = ["ChapterRequest", "GenerationResult", "VectorIndex", "WriterEngine"]
