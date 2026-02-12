from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

from gaiden.openai_client import get_client


CHUNKS_BASE = Path("data/chunks")
TRANSLATED_BASE = Path("data/translated")

DEFAULT_MODEL = "gpt-5.1"

SYSTEM_PROMPT = """You are a literary editor modernizing classic English fiction
for a 2025 audience, with an Oxford-educated tone.

You receive one chunk of an already-cleaned public domain text.
Your task is NOT to translate, but to modernize and polish the English while
preserving the original meaning, pacing, and narrative voice.

Rules:
- Keep the content in English.
- Remove archaic constructions and outdated turns of phrase.
- Sentence length: mostly between 6 and 14 words, with variation < 5%.
- Eliminate redundancies and wordy filler while preserving all information.
- Keep Sherlock Holmes' and Dr. Watson's voices consistent and recognizable.
- Maintain paragraph breaks and overall structure already present in the chunk.
- Do NOT add explanations, commentary, headings, or summaries.
- Output ONLY the modernized text, nothing else.
"""


@dataclass
class ChunkInfo:
    book_id: int
    chunk_index: int
    path: Path
    text: str


def iter_chunks_for_book(book_id: int) -> Iterable[ChunkInfo]:
    """
    Itera sobre os arquivos de chunk de um livro:
    data/chunks/book_XXXX/en/NNNN.txt
    """
    book_dir = CHUNKS_BASE / f"book_{book_id:04d}" / "en"
    if not book_dir.exists():
        raise FileNotFoundError(f"Chunks não encontrados em {book_dir}")

    paths = sorted(book_dir.glob("ch_*_chunk_*.txt"))
    for i, p in enumerate(paths, start=1):
        txt = p.read_text(encoding="utf-8", errors="replace")
        yield ChunkInfo(book_id=book_id, chunk_index=i, path=p, text=txt)


def estimate_cost(
    n_chunks: int,
    avg_tokens: int = 1800,
    passes: int = 1,
    price_per_1k: float = 0.002,
) -> Tuple[float, int]:
    """
    Estimativa simples de custo: n_chunks * avg_tokens * passes * price_per_1k/1000
    price_per_1k está default para um valor baixo (gpt-5.1 style),
    ajuste se necessário.
    """
    total_tokens = n_chunks * avg_tokens * passes
    cost = (total_tokens / 1000.0) * price_per_1k
    return cost, total_tokens


def translate_chunk_text(text: str, model: str = DEFAULT_MODEL) -> str:
    """
    Envia um chunk de texto para o modelo e retorna o texto modernizado.
    """
    client = get_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def run_translate_for_book(
    book_id: int,
    model: str = DEFAULT_MODEL,
    limit: int | None = None,
    dry_run: bool = False,
) -> None:
    """
    Traduz (moderniza) todos os chunks de um livro, ou apenas 'limit' chunks se especificado.
    Salva saída em data/translated/book_XXXX/EN/ch_<NN>_chunk_<SEQ>.EN.txt
    e um arquivo unificado data/translated/book_XXXX/EN/merge_translate_EN.txt
    """
    chunks: List[ChunkInfo] = list(iter_chunks_for_book(book_id))
    if limit is not None:
        chunks = chunks[:limit]

    n = len(chunks)
    if n == 0:
        print(f"[book {book_id:04d}] Nenhum chunk encontrado.")
        return

    est_cost, est_tokens = estimate_cost(n_chunks=n)
    print(f"[book {book_id:04d}] Chunks a processar: {n}")
    print(f"   Estimativa de tokens: {est_tokens}")
    print(f"   Estimativa de custo:  ~ USD {est_cost:.4f}")
    if dry_run:
        print("Dry-run solicitado: não será feita chamada à OpenAI.")
        return

    out_dir = TRANSLATED_BASE / f"book_{book_id:04d}" / "EN"
    out_dir.mkdir(parents=True, exist_ok=True)

    merged: List[str] = []

    for info in chunks:
        print(f"[book {book_id:04d}] Chunk {info.chunk_index}/{n} → {info.path.name}")
        modern = translate_chunk_text(info.text, model=model)
        out_path = out_dir / f"{info.path.stem}.EN.txt"
        out_path.write_text(modern, encoding="utf-8")
        merged.append(modern)

    merged_book_path = TRANSLATED_BASE / f"book_{book_id:04d}" / "EN" / "merge_translate_EN.txt"
    merged_book_path.parent.mkdir(parents=True, exist_ok=True)
    merged_book_path.write_text("\n\n".join(merged), encoding="utf-8")

    print(f"[book {book_id:04d}] Tradução concluída.")
    print(f"  Arquivos por chunk: {out_dir}")
    print(f"  Arquivo unificado:  {merged_book_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Gaiden: modernizar inglês (2025 Oxford-style) de um livro por chunks."
    )
    parser.add_argument("book_id", type=int, help="ID do livro (ex: 1 para book_0001)")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Modelo OpenAI a ser usado (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limitar número de chunks (para testes).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só mostrar estimativa de custo, sem chamar OpenAI.",
    )

    args = parser.parse_args()
    run_translate_for_book(
        book_id=args.book_id,
        model=args.model,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
