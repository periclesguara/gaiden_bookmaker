from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import List, Optional


BASE_DIR = Path(__file__).resolve().parents[1]
TRANSLATED_ROOT = BASE_DIR / "data" / "translated"
DB_PATH = BASE_DIR / "data" / "db" / "gaiden.sqlite3"


def _book_dir(book_id: int) -> Path:
    return TRANSLATED_ROOT / f"book_{book_id:04d}"


def list_languages_for_book(book_id: int) -> List[str]:
    """
    Lista os diretórios de idiomas existentes para um book_id.
    Ex.: ["en_modern_2026", "ptbr_2025", "es_2025", "de_krimi_2025"]
    """
    book_dir = _book_dir(book_id)
    if not book_dir.is_dir():
        return []
    langs: List[str] = []
    for child in sorted(book_dir.iterdir()):
        if child.is_dir():
            langs.append(child.name)
    return langs


def merge_language(book_id: int, lang_key: str, *, suffix: str | None = None) -> Optional[Path]:
    """
    Faz o merge de todos os chunks .txt de um idioma em um único arquivo.

    Entrada:
      - book_id: ex. 1 -> book_0001
      - lang_key: ex. "en_modern_2026", "ptbr_2025", "es_2025"
      - suffix (opcional): sufixo do arquivo de saída; se None, usa f"merged_{lang_key}.txt"

    Saída:
      - Path do arquivo mesclado.

    Convenção de diretórios atual do Gaiden:
      data/translated/book_0001/<lang_key>/0001.txt ... 0084.txt

    Saída:
      data/translated/book_0001/<lang_key>/merged_<lang_key>.txt
      (ou outro nome, se suffix for passado).
    """
    book_dir = _book_dir(book_id)
    in_dir = book_dir / lang_key

    if not in_dir.is_dir():
        raise FileNotFoundError(f"Diretório de tradução não encontrado: {in_dir}")

    txt_files = sorted(
        p for p in in_dir.glob("*.txt")
        if not (p.name.startswith("merged_") or p.name == "merged.txt")
    )
    if not txt_files:
        print(f"[WARN] Nenhum .txt encontrado em {in_dir} (ignorando).")
        return None

    if suffix is None:
        out_name = f"merged_{lang_key}.txt"
    else:
        out_name = suffix

    out_path = in_dir / out_name

    parts: List[str] = []
    for path in txt_files:
        text = path.read_text(encoding="utf-8").rstrip()
        if text:
            parts.append(text)

    # separa chunks por duas quebras de linha para marcar "fronteira de bloco"
    merged = "\n\n".join(parts) + "\n"

    out_path.write_text(merged, encoding="utf-8")

    register_merged_translation(
        book_id=book_id,
        lang_key=lang_key,
        out_path=out_path,
        merged_text=merged,
        chunk_count=len(txt_files),
        source_dir=in_dir,
    )

    print(f"[MERGE] book_{book_id:04d} / {lang_key} -> {out_path}")
    return out_path


def merge_all_for_book(book_id: int) -> list[Path]:
    """
    Faz merge para todos os idiomas que têm diretório, exceto dirs já mesclados (merged_*).
    """
    langs = list_languages_for_book(book_id)
    out_paths: list[Path] = []
    for lang in langs:
        # evita re-mesclar o próprio arquivo de saída
        if lang.startswith("merged_"):
            continue
        out_path = merge_language(book_id, lang)
        if out_path:
            out_paths.append(out_path)
    return out_paths


def _ensure_merged_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_translated_merged (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            lang_key TEXT NOT NULL,
            merged_path TEXT,
            merged_text TEXT,
            merged_sha256 TEXT,
            chunk_count INTEGER,
            source_dir TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE (book_id, lang_key)
        );
        """
    )
    conn.commit()


def register_merged_translation(
    *,
    book_id: int,
    lang_key: str,
    out_path: Path,
    merged_text: str,
    chunk_count: int,
    source_dir: Path,
) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(merged_text.encode("utf-8")).hexdigest()
    with sqlite3.connect(DB_PATH.as_posix()) as conn:
        _ensure_merged_table(conn)
        conn.execute(
            """
            INSERT INTO book_translated_merged (
                book_id,
                lang_key,
                merged_path,
                merged_text,
                merged_sha256,
                chunk_count,
                source_dir
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(book_id, lang_key) DO UPDATE SET
                merged_path = excluded.merged_path,
                merged_text = excluded.merged_text,
                merged_sha256 = excluded.merged_sha256,
                chunk_count = excluded.chunk_count,
                source_dir = excluded.source_dir,
                created_at = datetime('now')
            """,
            (
                book_id,
                lang_key,
                out_path.as_posix(),
                merged_text,
                sha,
                chunk_count,
                source_dir.as_posix(),
            ),
        )
        conn.commit()


def register_existing_merges() -> list[Path]:
    """
    Registra no banco merges já existentes em data/translated.

    Procura por arquivos merged_*.txt (ou merged.txt) em cada idioma e book.
    """
    if not TRANSLATED_ROOT.is_dir():
        return []

    registered: list[Path] = []
    for book_dir in sorted(TRANSLATED_ROOT.iterdir()):
        if not book_dir.is_dir() or not book_dir.name.startswith("book_"):
            continue

        try:
            book_id = int(book_dir.name.split("_", 1)[1])
        except (IndexError, ValueError):
            continue

        for lang_dir in sorted(book_dir.iterdir()):
            if not lang_dir.is_dir():
                continue

            lang_key = lang_dir.name
            preferred = lang_dir / f"merged_{lang_key}.txt"
            candidates = [preferred]
            candidates.extend(sorted(lang_dir.glob("merged_*.txt")))
            candidates.append(lang_dir / "merged.txt")

            merged_path = next((p for p in candidates if p.is_file()), None)
            if not merged_path:
                continue

            merged_text = merged_path.read_text(encoding="utf-8")
            chunk_count = len(
                [
                    p
                    for p in lang_dir.glob("*.txt")
                    if not (p.name.startswith("merged_") or p.name == "merged.txt")
                ]
            )

            register_merged_translation(
                book_id=book_id,
                lang_key=lang_key,
                out_path=merged_path,
                merged_text=merged_text,
                chunk_count=chunk_count,
                source_dir=lang_dir,
            )
            registered.append(merged_path)

    return registered


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso:")
        print("  python -m gaiden.merge_translated <book_id> [lang_key]")
        print("")
        print("Exemplos:")
        print("  python -m gaiden.merge_translated 1")
        print("  python -m gaiden.merge_translated 1 en_modern_2026")
        raise SystemExit(1)

    book_id = int(sys.argv[1])
    if len(sys.argv) == 2:
        # merge de todos os idiomas disponíveis
        paths = merge_all_for_book(book_id)
        print("[INFO] Arquivos mesclados:")
        for p in paths:
            print("  -", p.relative_to(BASE_DIR))
    else:
        lang = sys.argv[2]
        p = merge_language(book_id, lang)
        if p:
            print("[INFO] Arquivo mesclado:", p.relative_to(BASE_DIR))
        else:
            print("[INFO] Nenhum arquivo mesclado.")
