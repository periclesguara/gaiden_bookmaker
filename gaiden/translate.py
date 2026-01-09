from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from openai import OpenAI

from .db import get_connection
from .secrets import get_openai_key


CONTRACTS_DIR = Path("contracts")

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """
    Retorna um client OpenAI configurado com a chave do Gaiden.
    Usa primeiro a chave do gaiden_key_manager, depois OPENAI_API_KEY do ambiente.
    """
    global _client
    if _client is not None:
        return _client

    key = get_openai_key() or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY não configurada. "
            "Use a página 'Gaiden – OpenAI API Key' para salvar a chave."
        )

    _client = OpenAI(api_key=key)
    return _client


@dataclass
class SplitItem:
    id: int
    item_index: int
    item_type: str
    label: str
    path: str


def load_contract(mode: str) -> dict:
    """
    Carrega o contrato JSON para o modo selecionado.
    Exemplos de mode: 'en_modern_2025', 'en_ptbr_2025', 'en_es_2025'.
    """
    path = CONTRACTS_DIR / f"{mode}.json"
    if not path.exists():
        raise FileNotFoundError(f"Contrato não encontrado para modo '{mode}': {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_system_prompt_from_contract(contract: dict) -> str:
    """
    Converte o contrato JSON em um prompt de sistema.
    """
    parts: list[str] = []

    parts.append(
        "You are a literary editor/translator working for the Gaiden BookMaker project."
    )
    parts.append(
        f"Source language: {contract.get('source_lang', 'en')}."
        f" Target language: {contract.get('target_lang', contract.get('target_language', 'en'))}."
    )

    goals = contract.get("goals", [])
    if goals:
        parts.append("Your main goals are:")
        for g in goals:
            parts.append(f"- {g}")

    sp = contract.get("sentence_policy", {})
    if sp:
        min_w = sp.get("preferred_min_words") or sp.get("min")
        max_w = sp.get("preferred_max_words") or sp.get("max")
        if min_w and max_w:
            parts.append(
                f"Prefer short, clear sentences, typically {min_w}–{max_w} words, "
                "with some variation allowed."
            )
        notes = sp.get("notes", [])
        for n in notes:
            parts.append(f"- {n}")

    tone = contract.get("tone", {})
    reg = tone.get("register")
    if reg:
        parts.append(f"Tone/register: {reg}")
    avoid = tone.get("avoid", [])
    if avoid:
        parts.append("Avoid:")
        for a in avoid:
            parts.append(f"- {a}")

    cons = contract.get("constraints", {})
    preserve = cons.get("preserve", [])
    forbid = cons.get("forbid", [])
    if preserve:
        parts.append("Always preserve:")
        for p in preserve:
            parts.append(f"- {p}")
    if forbid:
        parts.append("Do NOT use:")
        for fbd in forbid:
            parts.append(f"- {fbd}")

    instr = contract.get("instructions", [])
    if instr:
        parts.append("Follow these instructions strictly:")
        for i in instr:
            parts.append(f"- {i}")

    # regra final hard
    parts.append(
        "Do not summarize or explain. Return ONLY the final rewritten/translated text."
    )
    return "\n".join(parts)


def _ensure_translation_schema() -> None:
    """Garante que a tabela de traduções exista."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_translated_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                split_item_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                source_lang TEXT,
                target_lang TEXT,
                engine TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                text_original TEXT,
                text_translated TEXT,
                UNIQUE (book_id, split_item_id, mode)
            );
            """
        )
        conn.commit()


def get_translation_status(book_id: int, mode: str) -> Tuple[int, int]:
    """
    Retorna (n_traduzidos, n_total_splits) para um dado livro+mode.
    """
    _ensure_translation_schema()
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) FROM book_split_items WHERE book_id = ?;",
            (book_id,),
        )
        total = cur.fetchone()[0] or 0

        cur = conn.execute(
            """
            SELECT COUNT(*)
            FROM book_translated_chunks
            WHERE book_id = ? AND mode = ?;
            """,
            (book_id, mode),
        )
        done = cur.fetchone()[0] or 0

    return done, total


def _fetch_split_items(book_id: int) -> Iterable[SplitItem]:
    """
    Busca todos os splits de um livro, na ordem correta.
    Não filtra nada: chapter/section, label curta, etc.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT id, item_index, item_type, label, path
            FROM book_split_items
            WHERE book_id = ?
            ORDER BY item_index ASC, id ASC;
            """,
            (book_id,),
        )
        rows = cur.fetchall()

    for row in rows:
        yield SplitItem(
            id=row[0],
            item_index=row[1],
            item_type=row[2],
            label=row[3] or "",
            path=row[4],
        )


def _load_chunk_text(path: str) -> str:
    if not path:
        return ""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Chunk não encontrado: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _call_openai_translate(chunk_text: str, mode: str) -> str:
    """
    Chamada ao modelo usando o contrato adequado.
    Modelo fixo: gpt-5.1 (full).
    """
    client = _get_client()
    contract = load_contract(mode)
    system_prompt = build_system_prompt_from_contract(contract)

    resp = client.chat.completions.create(
        model="gpt-5.1",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chunk_text},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def run_translation_for_book(
    book_id: int,
    mode: str,
    overwrite: bool = False,
) -> Tuple[int, int]:
    """
    Traduz TODOS os chunks de um livro para o modo selecionado.
    - Não mexe em split/chunk.
    - Só consome book_split_items.path e escreve em book_translated_chunks.

    Retorna (n_traduzidos_nesta_execução, n_total_chunks).
    """
    _ensure_translation_schema()

    splits = list(_fetch_split_items(book_id))
    total = len(splits)
    if total == 0:
        return 0, 0

    # tenta inferir idiomas a partir do contrato (se existir)
    contract = load_contract(mode)
    from_lang = contract.get("source_lang", "en")
    to_lang = contract.get("target_lang", contract.get("target_language", "en"))
    engine = "gpt-5.1"

    translated_now = 0

    with get_connection() as conn:
        for s in splits:
            cur = conn.execute(
                """
                SELECT id FROM book_translated_chunks
                WHERE book_id = ? AND split_item_id = ? AND mode = ?;
                """,
                (book_id, s.id, mode),
            )
            row = cur.fetchone()
            if row and not overwrite:
                continue

            original_text = _load_chunk_text(s.path)
            translated_text = _call_openai_translate(original_text, mode)

            if row and overwrite:
                conn.execute(
                    """
                    UPDATE book_translated_chunks
                    SET text_original = ?, text_translated = ?, source_lang = ?, target_lang = ?, engine = ?, created_at = datetime('now')
                    WHERE id = ?;
                    """,
                    (
                        original_text,
                        translated_text,
                        from_lang,
                        to_lang,
                        engine,
                        row[0],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO book_translated_chunks (
                        book_id,
                        split_item_id,
                        mode,
                        source_lang,
                        target_lang,
                        engine,
                        text_original,
                        text_translated
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        book_id,
                        s.id,
                        mode,
                        from_lang,
                        to_lang,
                        engine,
                        original_text,
                        translated_text,
                    ),
                )

            translated_now += 1

        conn.commit()

    return translated_now, total
