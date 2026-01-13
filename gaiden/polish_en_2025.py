from __future__ import annotations

import json
from pathlib import Path
from typing import List

from . import db as db_mod
from .openai_client import get_client

# Caminho do contrato de polish
CONTRACT_PATH = Path(__file__).resolve().parent / "contracts" / "polish" / "en_polish_2025.json"

client = get_client()


def _load_contract() -> dict:
    with CONTRACT_PATH.open("r", encoding="utf-8") as f:
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


def run_polish_en_2025(book_id: int, lang_key: str = "en_modern_2025") -> None:
    contract = _load_contract()
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

    print(f"[INFO] Polish EN 2025 — book_id={book_id}, lang_key={lang_key}, model={model}")

    # Busca o texto traduzido/merged no DB
    row = db_mod.get_translated_merged(book_id, lang_key)
    merged_text: str = row["merged_text"]
    source_path: str = row["merged_path"]

    print(f"[INFO] Tamanho do texto de entrada: {len(merged_text)} caracteres")

    lines = _split_lines(merged_text)
    total = len(lines)
    print(f"[INFO] Linhas (parágrafos lógicos) detectados (input): {total}")

    polished_lines: List[str] = []

    for idx, line in enumerate(lines, start=1):
        # Mantém linhas vazias exatamente como estão
        if not line.strip():
            polished_lines.append(line)
            continue

        print(f"[POLISH] Linha {idx}/{total}...", flush=True)

        # Chamada ao modelo: uma linha por vez
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
                            "text": line,
                        }
                    ],
                },
            ],
        )

        out = _extract_output_text(resp)
        out = out.strip("\n\r")

        if "\n" in out:
            # Segurança extra: se o modelo tentar quebrar em várias linhas,
            # compacta de volta em uma única linha.
            out = " ".join(part.strip() for part in out.splitlines() if part.strip())

        polished_lines.append(out)

    # Reconstrói o texto polido preservando a estrutura de linhas
    polished_text = "\n".join(polished_lines)

    # Sanidade: mesma contagem de linhas
    if len(polished_lines) != total:
        raise RuntimeError(
            f"Polish alterou contagem de linhas: input={total}, output={len(polished_lines)}"
        )

    # Caminho de saída (default + possibilidade de override via contrato)
    book_dir = Path(f"data/chunks/book_{book_id:04d}/refine_en_01")
    output_dir = Path(contract.get("output_dir", book_dir))
    output_name = contract.get("output_filename", "merged_polished_en_2025.txt")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / output_name

    out_path.write_text(polished_text, encoding="utf-8")
    print(f"[INFO] Polish salvo em: {out_path}")

    # Registra no DB
    db_mod.insert_polished_merged(
        book_id=book_id,
        lang="en_modern_2025",
        variant=variant,
        source_kind="translated_merged",
        source_path=source_path,
        polished_path=str(out_path),
        model=model,
    )

    print("[INFO] book_polished_merged atualizado com sucesso.")


def main() -> None:
    run_polish_en_2025(book_id=1, lang_key="en_modern_2025")


if __name__ == "__main__":
    main()
