from __future__ import annotations

import json
from pathlib import Path
from typing import List

from . import db as db_mod
from .openai_client import get_client

DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parent / "contracts" / "polish" / "en_polish_2025.json"

client = get_client()
POLISH_BATCH_SIZE = 24
PARA_SEPARATOR = "\n<<<POLISH_PARA_BREAK>>>\n"


def _contract_path_for_lang_key(lang_key: str) -> Path:
    return DEFAULT_CONTRACT_PATH


def _load_contract(lang_key: str) -> dict:
    contract_path = _contract_path_for_lang_key(lang_key)
    with contract_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _split_lines(text: str) -> List[str]:
    """
    Mantém exatamente a mesma estrutura de linhas.
    Cada linha não vazia é tratada como 'parágrafo lógico'.
    """
    return text.splitlines()


def _extract_output_text(resp) -> str:
    """
    Extrai o texto do Responses API de forma defensiva,
    cobrindo diferenças de SDK (.output_text vs .output[0].content[0].text).
    """
    # Tentativa nova API
    try:
        return resp.output[0].content[0].text
    except Exception:
        pass

    # Tentativa fallback (alguns SDKs expõem .output_text)
    try:
        return resp.output_text
    except Exception:
        pass

    raise RuntimeError("Não foi possível extrair o texto do objeto de resposta do modelo.")


def _batched_nonempty_line_indexes(lines: List[str], batch_size: int) -> List[List[int]]:
    indexes = [idx for idx, line in enumerate(lines) if line.strip()]
    return [indexes[i : i + batch_size] for i in range(0, len(indexes), batch_size)]


def _parse_batched_output(output: str, expected_count: int) -> List[str]:
    parts = [part.strip("\n\r") for part in output.split(PARA_SEPARATOR)]
    if len(parts) != expected_count:
        raise RuntimeError(
            f"Polish batch alterou a estrutura: expected={expected_count}, got={len(parts)}"
        )
    cleaned: List[str] = []
    for part in parts:
        if "\n" in part:
            part = " ".join(segment.strip() for segment in part.splitlines() if segment.strip())
        cleaned.append(part)
    return cleaned


def _polish_single_paragraph(*, model: str, system_prompt: str, text: str) -> str:
    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        ],
    )
    out = _extract_output_text(resp).strip("\n\r")
    if "\n" in out:
        out = " ".join(segment.strip() for segment in out.splitlines() if segment.strip())
    return out


def _fallback_merged_source(book_id: int, lang_key: str) -> tuple[str, str]:
    book_code = f"book_{book_id:04d}"
    root = Path(__file__).resolve().parents[1]
    candidates = [
        root / "data" / "translated" / book_code / "merge_refine_clean.txt",
        root / "data" / "builds" / book_code / "en" / "merge_refine.txt",
    ]

    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8"), str(path)
    raise RuntimeError(
        f"translated_merged not found for book_id={book_id}, lang_key={lang_key}, and no fallback merge file was found."
    )


def _polish_text_to_path(
    *,
    merged_text: str,
    source_path: str,
    output_path: Path,
    book_id: int,
    lang_key: str,
) -> None:
    contract = _load_contract(lang_key)
    model = contract.get("model", "gpt-4o")
    system_prompt = contract.get(
        "system_prompt",
        (
            "You are a world-class English editor for literary fiction (2025-2026 modern English).\n"
            "Task: slightly polish style, rhythm, clarity and naturalness of EACH input paragraph.\n"
            "- Do NOT add, remove or reorder information.\n"
            "- Do NOT merge or split paragraphs.\n"
            "- For each input you receive, you must return exactly ONE paragraph.\n"
            "- Preserve punctuation and proper nouns as much as possible.\n"
            "- Target: premium modern English, level 9.5/10.\n"
        ),
    )
    variant = contract.get("variant", "polish_2025")

    print(f"[INFO] Polish EN — book_id={book_id}, lang_key={lang_key}, model={model}")

    print(f"[INFO] Tamanho do texto de entrada: {len(merged_text)} caracteres")

    lines = _split_lines(merged_text)
    total = len(lines)
    print(f"[INFO] Linhas (parágrafos lógicos) detectados (input): {total}")

    polished_lines: List[str] = list(lines)
    nonempty_batches = _batched_nonempty_line_indexes(lines, POLISH_BATCH_SIZE)
    total_batches = len(nonempty_batches)

    for batch_idx, indexes in enumerate(nonempty_batches, start=1):
        batch_lines = [lines[i] for i in indexes]
        first_line = indexes[0] + 1
        last_line = indexes[-1] + 1
        print(
            f"[POLISH] Bloco {batch_idx}/{total_batches} | linhas {first_line}-{last_line}...",
            flush=True,
        )

        user_prompt = (
            "Polish the following paragraphs. Preserve their order and meaning.\n"
            f"Return exactly {len(batch_lines)} paragraphs in the same order.\n"
            f"Separate each output paragraph using this exact delimiter:\n{PARA_SEPARATOR}\n"
            "Do not add commentary, numbering, labels, or extra delimiters.\n\n"
            + PARA_SEPARATOR.join(batch_lines)
        )

        resp = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        )

        out = _extract_output_text(resp).strip("\n\r")
        try:
            parsed_lines = _parse_batched_output(out, len(batch_lines))
        except RuntimeError:
            print(
                f"[POLISH] Fallback linha a linha no bloco {batch_idx}/{total_batches}...",
                flush=True,
            )
            parsed_lines = [
                _polish_single_paragraph(model=model, system_prompt=system_prompt, text=line)
                for line in batch_lines
            ]
        for line_index, polished_line in zip(indexes, parsed_lines):
            polished_lines[line_index] = polished_line

    # Reconstrói o texto polido preservando a estrutura de linhas
    polished_text = "\n".join(polished_lines)

    # Sanidade: mesma contagem de linhas
    if len(polished_lines) != total:
        raise RuntimeError(
            f"Polish alterou contagem de linhas: input={total}, output={len(polished_lines)}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(polished_text, encoding="utf-8")
    print(f"[INFO] Polish salvo em: {output_path}")

    # Registra no DB
    db_mod.insert_polished_merged(
        book_id=book_id,
        lang="en_modern_2026",
        variant=variant,
        source_kind="translated_merged",
        source_path=source_path,
        polished_path=str(output_path),
        model=model,
    )

    print("[INFO] book_polished_merged atualizado com sucesso.")


def run_polish_en_2025(book_id: int, lang_key: str = "en_modern_2026") -> None:
    try:
        row = db_mod.get_translated_merged(book_id, lang_key)
        merged_text = row["merged_text"]
        source_path = row["merged_path"]
    except RuntimeError:
        merged_text, source_path = _fallback_merged_source(book_id, lang_key)

    contract = _load_contract(lang_key)
    book_dir = Path(f"data/chunks/book_{book_id:04d}/refine_en_01")
    output_dir = Path(contract.get("output_dir", book_dir))
    output_name = contract.get("output_filename", "merged_polished_en_2025.txt")
    out_path = output_dir / output_name

    _polish_text_to_path(
        merged_text=merged_text,
        source_path=source_path,
        output_path=out_path,
        book_id=book_id,
        lang_key=lang_key,
    )


def run_polish_en_merged_file(
    *,
    book_id: int,
    lang_key: str,
    source_path: str | Path,
    output_path: str | Path,
) -> None:
    source_path = Path(source_path)
    output_path = Path(output_path)
    merged_text = source_path.read_text(encoding="utf-8")
    _polish_text_to_path(
        merged_text=merged_text,
        source_path=str(source_path),
        output_path=output_path,
        book_id=book_id,
        lang_key=lang_key,
    )


def main() -> None:
    run_polish_en_2025(book_id=1, lang_key="en_modern_2026")


if __name__ == "__main__":
    main()
