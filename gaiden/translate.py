from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from gaiden.lang import normalize_lang_code
from gaiden.openai_client import get_client, choose_model


def _load_contract(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Contrato não encontrado: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_target_language(contract: Dict[str, Any]) -> str:
    # tenta achar em vários campos, caindo em "en" se nada vier
    candidates = [
        ("target_language",),
        ("target_lang",),
        ("output_language",),
        ("output", "language"),
        ("output", "lang"),
    ]
    for path in candidates:
        val: Any = contract
        ok = True
        for key in path:
            if isinstance(val, dict) and key in val:
                val = val[key]
            else:
                ok = False
                break
        if ok and isinstance(val, str) and val.strip():
            return normalize_lang_code(val.strip(), default="en_modern")
    return normalize_lang_code("en_modern", default="en_modern")


def _build_messages(chunk_text: str, contract: Dict[str, Any]) -> List[Dict[str, str]]:
    system_prompt = (
        contract.get("system_prompt")
        or contract.get("system")
        or ""
    )
    user_template = contract.get("user_prompt") or contract.get("user") or "{text}"
    if "{{TEXT}}" in user_template:
        user_text = user_template.replace("{{TEXT}}", chunk_text)
    else:
        user_text = user_template.replace("{text}", chunk_text).replace("{TEXT}", chunk_text)

    messages: List[Dict[str, str]] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": user_text})
    return messages

def _detect_chunk_dir(contract: Dict[str, Any]) -> Path:
    chunk_dir = contract.get("chunk_dir")
    if chunk_dir:
        return Path(chunk_dir).expanduser()

    book_id = contract.get("book_id") or contract.get("book")
    if book_id is None and "GAIDEN_BOOK_ID" in os.environ:
        try:
            book_id = int(os.environ.get("GAIDEN_BOOK_ID", "").strip())
        except ValueError:
            book_id = None

    if isinstance(book_id, int):
        return Path("data/chunks") / f"book_{book_id:04d}" / "en"

    chunks_root = Path("data/chunks")
    if not chunks_root.is_dir():
        raise RuntimeError('Contrato não define "chunk_dir" e nenhum diretório data/chunks foi encontrado.')

    candidates = sorted(chunks_root.glob("book_*/en"))
    if not candidates:
        raise RuntimeError('Contrato não define "chunk_dir" e nenhum diretório "en" foi encontrado em data/chunks.')
    if len(candidates) > 1:
        raise RuntimeError(
            'Contrato não define "chunk_dir" e há múltiplos livros em data/chunks. '
            'Defina "chunk_dir" no contrato ou GAIDEN_BOOK_ID no ambiente.'
        )

    return candidates[0]

def _default_out_dir(chunk_dir_path: Path, target_lang: str) -> Path:
    parts = chunk_dir_path.parts
    if len(parts) >= 2 and parts[-1] == "en" and parts[-2].startswith("book_"):
        lang_dir = normalize_lang_code(target_lang, default="en_modern")
        return Path("data/translated") / parts[-2] / lang_dir
    return chunk_dir_path.parent / f"{chunk_dir_path.name}_{target_lang}"

def _extract_book_id(path: Path) -> Optional[int]:
    for part in path.parts:
        m = re.match(r"^book_(\d{4})$", part)
        if m:
            return int(m.group(1))
    return None

def _parse_chunk_filename(path: Path) -> Tuple[int, int]:
    m = re.match(r"^ch_(\d+)_chunk_(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"Chunk inválido (esperado ch_NN_chunk_SEQ.txt): {path.name}")
    return int(m.group(1)), int(m.group(2))


def _merge_translated_chunks(
    out_dir_path: Path,
    lang_key: str,
    book_id: Optional[int],
    expected_count: Optional[int] = None,
) -> Optional[Path]:
    pattern = f"ch_*_chunk_*.{lang_key}.txt"
    txt_files = sorted(out_dir_path.glob(pattern))
    if not txt_files:
        return None
    if expected_count is not None and len(txt_files) != expected_count:
        raise RuntimeError(
            f"Chunks traduzidos incompletos: {len(txt_files)}/{expected_count} em {out_dir_path}"
        )

    parts: List[str] = []
    for path in txt_files:
        text = path.read_text(encoding="utf-8").rstrip()
        if text:
            parts.append(text)

    merged = "\n\n".join(parts) + "\n"
    out_path = out_dir_path / f"merge_translate_{lang_key}.txt"
    out_path.write_text(merged, encoding="utf-8")

    if book_id is not None:
        from gaiden.merge_translated import register_merged_translation
        register_merged_translation(
            book_id=book_id,
            lang_key=lang_key,
            out_path=out_path,
            merged_text=merged,
            chunk_count=len(txt_files),
            source_dir=out_dir_path,
        )

    return out_path


def run_translate_with_contract(
    contract_path: str | Path,
    *,
    dry_run: bool = False,
    limit_chunks: Optional[int] = None,
    chunk_dir_override: Optional[str | Path] = None,
    out_dir_override: Optional[str | Path] = None,
) -> None:
    """
    Tradução file-based, guiada 100% pelo contrato JSON.

    Contrato deve ter, no mínimo:
      - "chunk_dir": diretório com NNNN.txt de entrada
      - "out_dir":   diretório de saída para NNNN.txt traduzidos

    Opcional:
      - "model": ex. "gpt-5.1" ou "gpt-5.2"
      - "temperature": float
      - "max_output_tokens": int
      - "system_prompt" / "system"
      - "user_prompt" / "user"
      - "output.language" / "target_language" etc. (usado só pra default/log)
    """
    contract = _load_contract(contract_path)

    out_dir = contract.get("out_dir")
    if chunk_dir_override:
        chunk_dir_str = str(chunk_dir_override)
        if "data/chunks/book_" not in chunk_dir_str or not chunk_dir_str.endswith("/en"):
            raise SystemExit("chunk-dir-override inválido. Esperado .../data/chunks/book_XXXX/en")
        chunk_dir_path = Path(chunk_dir_override).expanduser()
    else:
        chunk_dir_path = _detect_chunk_dir(contract)
    uses_book_placeholder = (
        isinstance(out_dir, str)
        and "{BOOK_ID}" in out_dir
        or isinstance(contract.get("chunk_dir"), str)
        and "{BOOK_ID}" in contract.get("chunk_dir")
    )
    if uses_book_placeholder and (not chunk_dir_override or not out_dir_override):
        raise SystemExit(
            "Contrato usa {BOOK_ID}. Use --chunk-dir-override e --out-dir-override "
            "(ou rode via ./scripts/translate_book.sh book_XXXX)."
        )
    if out_dir_override:
        out_dir_str = str(out_dir_override)
        if "data/translated/book_" not in out_dir_str:
            raise SystemExit("out-dir-override inválido. Esperado .../data/translated/book_XXXX/<LANG>")
    if not chunk_dir_path.is_dir():
        raise FileNotFoundError(f'chunk_dir não existe: {chunk_dir_path}')
    if "split" in str(chunk_dir_path):
        raise RuntimeError(f'chunk_dir inválido (split proibido): {chunk_dir_path}')

    if out_dir_override:
        out_dir_path = Path(out_dir_override).expanduser()
    elif not out_dir:
        # fallback: cria um sufixo baseado na língua alvo
        lang = _get_target_language(contract)
        out_dir_path = _default_out_dir(chunk_dir_path, lang)
    else:
        out_dir_path = Path(out_dir).expanduser()

    out_dir_path.mkdir(parents=True, exist_ok=True)

    model = contract.get("model", "")
    if contract.get("stage") == "translate" and contract.get("model_lock") is not True:
        raise RuntimeError("TRANSLATE MODEL VIOLATION: stage=translate requires model_lock=true")
    model_effective = choose_model(stage=contract.get("stage"), contract_model=model, env_default=None)
    temperature = float(contract.get("temperature", 0.4))
    max_output_tokens = int(contract.get("max_output_tokens", 1200))

    client = None if dry_run else get_client()

    txt_files = sorted(chunk_dir_path.glob("ch_*_chunk_*.txt"))
    if not txt_files:
        print(f"[WARN] Nenhum .txt encontrado em {chunk_dir_path}")
        return

    target_lang = _get_target_language(contract)
    lang_key = out_dir_path.name
    book_id = _extract_book_id(out_dir_path)

    print("[INFO] Tradução file-based iniciada")
    print(f"  Contrato: {contract_path}")
    print(f"  Língua alvo: {target_lang}")
    print(f"  Modelo: {model}")
    print(f"  Origem chunks: {chunk_dir_path}")
    print(f"  Destino chunks: {out_dir_path}")
    print(f"  Total de chunks: {len(txt_files)}")

    chunk_entries: List[Tuple[int, int, Path]] = []
    for chunk_path in txt_files:
        ch_num, seq = _parse_chunk_filename(chunk_path)
        chunk_entries.append((ch_num, seq, chunk_path))
    chunk_entries.sort(key=lambda x: (x[0], x[1]))

    if limit_chunks is not None and limit_chunks > 0:
        chunk_entries = chunk_entries[:limit_chunks]

    for idx, (_, __, chunk_path) in enumerate(chunk_entries, start=1):
        text = chunk_path.read_text(encoding="utf-8").strip()
        if not text:
            continue

        if dry_run:
            translated = f"[DRY-RUN {lang_key}] {chunk_path.name}\n\n{text}\n"
        else:
            messages = _build_messages(text, contract)

            resp = client.responses.create(
        model=model_effective,
                input=messages,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )

            # tenta pegar o texto da forma mais genérica possível
            translated = ""
            try:
                translated = getattr(resp, "output_text", "").strip()
            except Exception:
                translated = ""

            if not translated:
                try:
                    translated = resp.output[0].content[0].text.strip()
                except Exception:
                    translated = ""

            if not translated.strip():
                raise RuntimeError(f"Tradução vazia para {chunk_path.name}")

        out_path = out_dir_path / f"{chunk_path.stem}.{lang_key}.txt"
        out_path.write_text(translated, encoding="utf-8")

        print(f"[OK] {idx:04d}/{len(txt_files):04d} -> {out_path}")

    print("[INFO] Tradução concluída.")
    merged_path = _merge_translated_chunks(
        out_dir_path,
        lang_key,
        book_id,
        expected_count=len(chunk_entries),
    )
    if merged_path:
        print(f"[INFO] Arquivo unificado: {merged_path}")


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("contract", type=str, help="Caminho do contrato JSON")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--limit-chunks", type=int, default=None)
    parser.add_argument("--chunk-dir-override", type=str, default=None)
    parser.add_argument("--out-dir-override", type=str, default=None)
    args = parser.parse_args()

    run_translate_with_contract(
        args.contract,
        dry_run=args.dry_run,
        limit_chunks=args.limit_chunks,
        chunk_dir_override=args.chunk_dir_override,
        out_dir_override=args.out_dir_override,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Uso: python -m gaiden.translate <caminho_contrato.json>")
        raise SystemExit(1)

    run_translate_with_contract(sys.argv[1])
