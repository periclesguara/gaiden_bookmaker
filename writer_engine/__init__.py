"""Local-first RAG and book-writing engine for Gaiden."""

from .engine import ChapterRequest, GenerationResult, NonfictionRequest, WriterEngine
from .index import VectorIndex

__all__ = ["ChapterRequest", "GenerationResult", "NonfictionRequest", "VectorIndex", "WriterEngine"]
