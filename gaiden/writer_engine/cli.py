from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from .clients import OpenAIEmbeddingClient, QwenGenerator
from .engine import ChapterRequest, WriterEngine
from .index import VectorIndex


def _embedding_client() -> OpenAIEmbeddingClient:
    return OpenAIEmbeddingClient(
        base_url=os.environ.get("GAIDEN_EMBEDDING_BASE_URL", "http://127.0.0.1:8001/v1"),
        api_key=os.environ.get("GAIDEN_EMBEDDING_API_KEY", "EMPTY"),
        model=os.environ.get("GAIDEN_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
    )


def _generator() -> QwenGenerator:
    return QwenGenerator(
        base_url=os.environ.get("GAIDEN_QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
        api_key=os.environ.get("GAIDEN_QWEN_API_KEY", "EMPTY"),
        model=os.environ.get("GAIDEN_QWEN_MODEL", "Qwen/Qwen3.5-9B"),
        thinking=os.environ.get("GAIDEN_QWEN_THINKING", "0").casefold()
        in {"1", "true", "yes", "on"},
    )


def _write_exclusive(path: Path, content: str) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _cmd_index(args: argparse.Namespace) -> None:
    index = VectorIndex.build(Path(args.source_root), _embedding_client())
    index.save(Path(args.index))
    print(json.dumps({
        "status": "indexed",
        "sources": len({chunk.source_path for chunk, _ in index.rows}),
        "chunks": len(index.rows),
        "model": index.model,
        "dimension": index.dimension,
        "index": str(Path(args.index).expanduser().resolve()),
    }, ensure_ascii=False))


def _cmd_query(args: argparse.Namespace) -> None:
    index = VectorIndex.load(Path(args.index))
    hits = index.search(args.query, _embedding_client(), top_k=args.top_k)
    print(json.dumps([
        {"chunk_id": hit.chunk.chunk_id, "source": hit.chunk.source_path,
         "heading": hit.chunk.heading, "score": hit.score}
        for hit in hits
    ], ensure_ascii=False, indent=2))


def _cmd_chapter(args: argparse.Namespace) -> None:
    request_data = json.loads(Path(args.request).read_text(encoding="utf-8"))
    request = ChapterRequest(**request_data)
    engine = WriterEngine(
        index=VectorIndex.load(Path(args.index)),
        embedder=_embedding_client(),
        generator=_generator(),
    )
    result = engine.create_chapter(request, top_k=args.top_k)
    output = Path(args.output)
    _write_exclusive(output, result.text + "\n")
    audit_path = output.with_suffix(output.suffix + ".audit.json")
    _write_exclusive(audit_path, json.dumps({
        "status": "DRAFT",
        "request": asdict(request),
        "generation_model": result.model,
        "source_chunk_ids": result.source_chunk_ids,
        "source_scores": result.source_scores,
    }, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "DRAFT", "output": str(output), "audit": str(audit_path)}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gaiden local Writer engine")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="atomically rebuild a complete corpus index")
    index.add_argument("--source-root", required=True)
    index.add_argument("--index", required=True)
    index.set_defaults(func=_cmd_index)
    query = commands.add_parser("query", help="inspect retrieval metadata")
    query.add_argument("--index", required=True)
    query.add_argument("--query", required=True)
    query.add_argument("--top-k", type=int, default=8)
    query.set_defaults(func=_cmd_query)
    chapter = commands.add_parser("chapter", help="create an unapproved chapter draft")
    chapter.add_argument("--index", required=True)
    chapter.add_argument("--request", required=True)
    chapter.add_argument("--output", required=True)
    chapter.add_argument("--top-k", type=int, default=8)
    chapter.set_defaults(func=_cmd_chapter)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
