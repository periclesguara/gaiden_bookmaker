from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .clients import Embedder
from .corpus import SourceChunk, load_corpus

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SearchHit:
    score: float
    chunk: SourceChunk


def _normalize(vector: list[float]) -> list[float]:
    if not vector or any(not math.isfinite(float(value)) for value in vector):
        raise ValueError("embedding contains invalid values")
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if norm == 0:
        raise ValueError("embedding vector has zero norm")
    return [float(value) / norm for value in vector]


class VectorIndex:
    def __init__(self, *, model: str, dimension: int, rows: list[tuple[SourceChunk, list[float]]]):
        self.model = model
        self.dimension = dimension
        self.rows = rows

    @classmethod
    def build(cls, source_root: Path, embedder: Embedder) -> "VectorIndex":
        sources, chunks = load_corpus(source_root)
        vectors = embedder.embed([chunk.text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise RuntimeError("not every corpus chunk received an embedding")
        normalized = [_normalize(vector) for vector in vectors]
        dimensions = {len(vector) for vector in normalized}
        if len(dimensions) != 1:
            raise ValueError("embedding endpoint returned mixed vector dimensions")
        index = cls(
            model=embedder.model,
            dimension=dimensions.pop(),
            rows=list(zip(chunks, normalized, strict=True)),
        )
        index.source_count = len(sources)
        return index

    def save(self, path: Path) -> None:
        destination = path.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_paths = {chunk.source_path for chunk, _ in self.rows}
        header = {
            "record_type": "header",
            "schema_version": SCHEMA_VERSION,
            "embedding_model": self.model,
            "dimension": self.dimension,
            "source_count": len(source_paths),
            "chunk_count": len(self.rows),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        fd, temporary_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(header, ensure_ascii=False) + "\n")
                for chunk, vector in self.rows:
                    handle.write(
                        json.dumps(
                            {
                                "record_type": "chunk",
                                "chunk": asdict(chunk),
                                "vector": vector,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path) -> "VectorIndex":
        with path.expanduser().resolve(strict=True).open(encoding="utf-8") as handle:
            header = json.loads(handle.readline())
            if header.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported vector index schema")
            dimension = int(header["dimension"])
            rows: list[tuple[SourceChunk, list[float]]] = []
            for line in handle:
                record = json.loads(line)
                if record.get("record_type") != "chunk":
                    raise ValueError("invalid index record")
                vector = _normalize(record["vector"])
                if len(vector) != dimension:
                    raise ValueError("index vector dimension mismatch")
                rows.append((SourceChunk(**record["chunk"]), vector))
        if len(rows) != int(header["chunk_count"]):
            raise ValueError("truncated vector index")
        if len({chunk.source_path for chunk, _ in rows}) != int(header["source_count"]):
            raise ValueError("vector index source manifest mismatch")
        return cls(model=header["embedding_model"], dimension=dimension, rows=rows)

    def search(self, query: str, embedder: Embedder, *, top_k: int = 8) -> list[SearchHit]:
        if embedder.model != self.model:
            raise ValueError("query embedding model does not match the index")
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        query_vectors = embedder.embed([query])
        if len(query_vectors) != 1:
            raise RuntimeError("query did not receive exactly one embedding")
        query_vector = _normalize(query_vectors[0])
        if len(query_vector) != self.dimension:
            raise ValueError("query vector dimension does not match the index")
        hits = [
            SearchHit(score=sum(a * b for a, b in zip(query_vector, vector, strict=True)), chunk=chunk)
            for chunk, vector in self.rows
        ]
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.chunk_id))[:top_k]
