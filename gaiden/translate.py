from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

from gaiden.openai_client import get_client


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
            return val.strip()
    return "en"


def _build_messages(chunk_text: str, contract: Dict[str, Any]) -> List[Dict[str, str]]:
    system_prompt = (
        contract.get("system_prompt")
        or contract.get("system")
        or ""
    )
    user_template = contract.get("user_prompt") or contract.get("user") or "{text}"
    user_text = user_template.replace("{text}", chunk_text)

    messages: List[Dict[str, str]] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": user_text})
    return messages


_META_OUTPUT_PATTERNS = [
    re.compile(
        r"^please\s+(provide|paste|share|send)\s+(the\s+)?(passage|text|excerpt|chapter|content)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(what|which)\s+(passage|text|excerpt|chapter|content)\b.*\b(would you like|do you want)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(here is|here's|output:|translated text:|translation:|rewrite:|rewritten text:|summary:|analysis:|note:)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(aqui est[áa]|texto traduzido:|tradu[cç][ãa]o:|resumo:|an[aá]lise:|nota:)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(aqu[ií] est[áa]|texto traducido:|traducci[oó]n:|resumen:|an[aá]lisis:|nota:)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(hier ist|übersetzung:|zusammenfassung:|analyse:|hinweis:)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(of course|sure|certainly|claro|por supuesto|natürlich|i'm sorry|lo siento|desculpe|es tut mir leid)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(this passage|the passage|este trecho|este fragmento|este pasaje|dieser abschnitt)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\d+\.\s"),
    re.compile(r"^[-*]\s"),
]


_CHUNK_COMPLETE_END_PATTERNS = [
    re.compile(r"^#{1,6}\s+\S"),
    re.compile(r"^(chapter|book|part)\b", re.IGNORECASE),
    re.compile(r"^[IVXLC0-9]+[.)]?$"),
]
_CHUNK_COMPLETE_END_CHARS = set('.!?…:;"”’\')]}')


def _sanitize_translated_output(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    kept: List[str] = []
    for para in paragraphs:
        if any(pattern.search(para) for pattern in _META_OUTPUT_PATTERNS):
            continue
        kept.append(para)

    cleaned = "\n\n".join(kept).strip()
    if not cleaned:
        raise RuntimeError("Model output was empty after removing meta/commentary text.")

    first_line = cleaned.splitlines()[0].strip()
    if any(pattern.search(first_line) for pattern in _META_OUTPUT_PATTERNS):
        raise RuntimeError(f"Model output still contains meta/commentary text: {first_line[:120]}")

    return cleaned


def sanitize_generated_chunk_text(text: str) -> str:
    return _sanitize_translated_output(text)


def text_has_complete_chunk_boundary(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False

    last_line = stripped.splitlines()[-1].strip()
    if not last_line:
        return False

    if any(pattern.match(last_line) for pattern in _CHUNK_COMPLETE_END_PATTERNS):
        return True
    return stripped[-1] in _CHUNK_COMPLETE_END_CHARS


def chunk_truncation_reason(source_text: str, candidate_text: str) -> Optional[str]:
    source_clean = source_text.strip()
    candidate_clean = candidate_text.strip()
    if not source_clean or not candidate_clean:
        return None
    if not text_has_complete_chunk_boundary(source_clean):
        return None
    if text_has_complete_chunk_boundary(candidate_clean):
        return None

    source_tail = re.sub(r"\s+", " ", source_clean[-180:]).strip()
    candidate_tail = re.sub(r"\s+", " ", candidate_clean[-180:]).strip()
    return (
        "Model output appears truncated before the chunk boundary. "
        f"output_tail={candidate_tail!r} source_tail={source_tail!r}"
    )


def assert_chunk_not_truncated(source_text: str, candidate_text: str, chunk_name: str) -> None:
    reason = chunk_truncation_reason(source_text, candidate_text)
    if reason:
        raise RuntimeError(reason)


def _sanitize_with_contract_fallback(
    translated_text: str,
    source_text: str,
    contract: Dict[str, Any],
    chunk_name: str,
) -> str:
    try:
        return _sanitize_translated_output(translated_text)
    except RuntimeError as exc:
        fallback_mode = str(contract.get("sanitize_failure_fallback") or "").strip().lower()
        if fallback_mode != "keep_source_chunk":
            raise

        source_clean = source_text.strip()
        if not source_clean:
            raise

        print(
            f"[WARN] sanitize fallback for {chunk_name}: {exc}. "
            "Keeping source chunk unchanged."
        )
        return source_clean


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
        return Path("data/chunks") / f"book_{book_id:04d}" / "split_01"

    chunks_root = Path("data/chunks")
    if not chunks_root.is_dir():
        raise RuntimeError('Contrato não define "chunk_dir" e nenhum diretório data/chunks foi encontrado.')

    candidates = sorted(chunks_root.glob("book_*/split_01"))
    if not candidates:
        raise RuntimeError('Contrato não define "chunk_dir" e nenhum split_01 foi encontrado em data/chunks.')
    if len(candidates) > 1:
        raise RuntimeError(
            'Contrato não define "chunk_dir" e há múltiplos livros em data/chunks. '
            'Defina "chunk_dir" no contrato ou GAIDEN_BOOK_ID no ambiente.'
        )

    return candidates[0]

def _default_out_dir(chunk_dir_path: Path, target_lang: str) -> Path:
    parts = chunk_dir_path.parts
    if len(parts) >= 2 and parts[-1].startswith("split_") and parts[-2].startswith("book_"):
        return Path("data/translated") / parts[-2] / parts[-1] / target_lang
    return chunk_dir_path.parent / f"{chunk_dir_path.name}_{target_lang}"

def _extract_book_id(path: Path) -> Optional[int]:
    for part in path.parts:
        m = re.match(r"^book_(\d{4})$", part)
        if m:
            return int(m.group(1))
    return None

def _merge_translated_chunks(out_dir_path: Path, lang_key: str, book_id: Optional[int]) -> Optional[Path]:
    txt_files = sorted(
        p for p in out_dir_path.glob("*.txt")
        if not (p.name.startswith("merged_") or p.name == "merged.txt")
    )
    if not txt_files:
        return None

    parts: List[str] = []
    for path in txt_files:
        text = path.read_text(encoding="utf-8").rstrip()
        if text:
            parts.append(text)

    merged = "\n\n".join(parts) + "\n"
    out_path = out_dir_path / f"merged_{lang_key}.txt"
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


def run_translate_with_contract(contract_path: str | Path) -> None:
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
    chunk_dir_path = _detect_chunk_dir(contract)
    if not chunk_dir_path.is_dir():
        raise FileNotFoundError(f'chunk_dir não existe: {chunk_dir_path}')

    if not out_dir:
        # fallback: cria um sufixo baseado na língua alvo
        lang = _get_target_language(contract)
        out_dir_path = _default_out_dir(chunk_dir_path, lang)
    else:
        out_dir_path = Path(out_dir).expanduser()

    out_dir_path.mkdir(parents=True, exist_ok=True)

    model = contract.get("model", "gpt-5.1")
    temperature = float(contract.get("temperature", 0.4))
    max_output_tokens = int(contract.get("max_output_tokens", 1200))

    client = get_client()

    txt_files = sorted(chunk_dir_path.glob("*.txt"))
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

    for idx, chunk_path in enumerate(txt_files, start=1):
        text = chunk_path.read_text(encoding="utf-8").strip()
        if not text:
            continue

        messages = _build_messages(text, contract)

        resp = client.responses.create(
            model=model,
            input=messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        # tenta pegar o texto da forma mais genérica possível
        translated = ""
        try:
            # algumas versões têm output_text
            translated = getattr(resp, "output_text", "").strip()
        except Exception:
            translated = ""

        if not translated:
            # fallback para estrutura output[0].content[0].text
            try:
                translated = resp.output[0].content[0].text.strip()
            except Exception:
                translated = ""

        try:
            translated = _sanitize_with_contract_fallback(
                translated,
                text,
                contract,
                chunk_path.name,
            )
            assert_chunk_not_truncated(text, translated, chunk_path.name)
        except Exception as exc:
            raw_preview = re.sub(r"\s+", " ", translated).strip()[:200]
            raise RuntimeError(
                f"{chunk_path.name}: {exc}. raw_preview={raw_preview!r}"
            ) from exc

        out_path = out_dir_path / chunk_path.name
        out_path.write_text(translated, encoding="utf-8")

        print(f"[OK] {idx:04d}/{len(txt_files):04d} -> {out_path}")

    print("[INFO] Tradução concluída.")
    merged_path = _merge_translated_chunks(out_dir_path, lang_key, book_id)
    if merged_path:
        print(f"[INFO] Arquivo unificado: {merged_path}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Uso: python -m gaiden.translate <caminho_contrato.json>")
        raise SystemExit(1)

    run_translate_with_contract(sys.argv[1])
