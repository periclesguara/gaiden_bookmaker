from __future__ import annotations

from dataclasses import dataclass

from .clients import Embedder
from .index import SearchHit, VectorIndex


@dataclass(frozen=True)
class Retrieval:
    query: str
    hits: tuple[SearchHit, ...]
    context: str


def retrieve(
    index: VectorIndex,
    embedder: Embedder,
    query: str,
    *,
    top_k: int = 8,
    max_context_chars: int = 24000,
) -> Retrieval:
    if not query.strip():
        raise ValueError("retrieval query cannot be empty")
    hits = index.search(query.strip(), embedder, top_k=top_k)
    blocks: list[str] = []
    kept: list[SearchHit] = []
    used = 0
    for number, hit in enumerate(hits, start=1):
        block = (
            f"[S{number}] source={hit.chunk.source_path} "
            f"heading={hit.chunk.heading!r} chunk={hit.chunk.chunk_id} "
            f"score={hit.score:.6f}\n{hit.chunk.text}"
        )
        if kept and used + len(block) > max_context_chars:
            break
        blocks.append(block)
        kept.append(hit)
        used += len(block)
    return Retrieval(query=query.strip(), hits=tuple(kept), context="\n\n".join(blocks))
