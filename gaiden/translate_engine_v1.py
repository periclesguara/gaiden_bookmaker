from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# IMPORTANT:
# This engine is intentionally minimal and "add-only".
# It does not refactor existing gaiden.translate.py.
# It provides a stable CLI bridge for Matrix Gaiden.

DEFAULT_FILE_GLOB = "ch_*_chunk_*.txt"

UNIVERSAL_SYSTEM_PROMPT = """You are a senior literary translator.

HARD CONSTRAINTS:
- Do NOT summarize, cut, expand, or reorder content.
- Do NOT change paragraph structure.
- Do NOT add titles, indexes, footnotes, notes, or frontmatter.
- Do NOT use Markdown.
- Do NOT add comments or explanations.
- Do NOT alter proper names, places, dates, or chapter numbering semantics.

QUALITY RULES (Modern 2026):
- Produce a modern, natural, fluent translation.
- Reduce machine-translation redundancy (accidental repeated words).
- Reduce archaic phrasing when it harms readability, without changing meaning.
- If a sentence is excessively long, you may split it into shorter sentences ONLY to improve clarity and ONLY without meaning loss.
- Keep tone and narrative intent.

Return ONLY the translated text.
"""

LANG_TARGET_LABELS = {
    # "en_modern" is still English output; treat as controlled modernization.
    "en_modern": "Modern English (2026)",
    "de": "Modern German (2026)",
    "fr": "Modern French (2026)",
    "es": "Modern Spanish (Latin American neutral, 2026)",
    "ptbr": "Modern Brazilian Portuguese (neutral, 2026)",
    "it": "Modern Italian (2026)",
}

def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def _write_text(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")

def _sorted_chunk_files(chunks_dir: Path, file_glob: str) -> List[Path]:
    files = sorted([p for p in chunks_dir.glob(file_glob) if p.is_file()])
    return files

def _load_json(p: Path) -> Dict:
    return json.loads(_read_text(p))

def _dump_json(p: Path, data: Dict) -> None:
    _write_text(p, json.dumps(data, ensure_ascii=False, indent=2))

def _make_lang_system_prompt(target_lang: str) -> str:
    label = LANG_TARGET_LABELS.get(target_lang, target_lang)
    return f"{UNIVERSAL_SYSTEM_PROMPT}\n\nTARGET LANGUAGE: {label}\n"

def call_openai_gpt52_translate(text: str, system_prompt: str) -> str:
    """
    Minimal OpenAI GPT-5.2 call.
    REQUIREMENTS:
    - Must run with existing env already configured in your repo.
    - This uses the official OpenAI SDK if installed, otherwise raises.
    - No streaming. Deterministic params.
    """
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError(
            "OpenAI SDK not available. Install/configure it in the venv, or adapt this call to gaiden/openai_client.py"
        ) from e

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment.")

    client = OpenAI(api_key=api_key)

    # NOTE: GPT-5.2 usage varies depending on SDK version.
    # We keep it intentionally simple: chat.completions with deterministic temperature.
    # If your repo already wraps OpenAI calls, replace this function to call your wrapper.
    resp = client.chat.completions.create(
        model="gpt-5.2",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.1,
    )

    out = resp.choices[0].message.content or ""
    return out.strip("\n") + "\n"

def translate_book_chunks(
    book: str,
    source_lang: str,
    target_lang: str,
    chunks_root: Path,
    translated_root: Path,
    file_glob: str = DEFAULT_FILE_GLOB,
    resume: bool = True,
    dry_run: bool = True,
) -> Dict:
    in_dir = chunks_root / book / source_lang
    out_dir = translated_root / book / target_lang
    _ensure_dir(out_dir)

    files = _sorted_chunk_files(in_dir, file_glob)
    if not files:
        raise RuntimeError(f"No chunk files found: {in_dir}/{file_glob}")

    report = {
        "schema": "gaiden_translate_run_report_v1",
        "book": book,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "started_at": _utc_now(),
        "input_dir": str(in_dir),
        "output_dir": str(out_dir),
        "file_glob": file_glob,
        "items": [],
    }

    system_prompt = _make_lang_system_prompt(target_lang)

    for fp in files:
        out_fp = out_dir / fp.name

        if resume and out_fp.exists():
            report["items"].append({"chunk_file": fp.name, "status": "skipped_exists", "output_path": str(out_fp)})
            continue

        src = _read_text(fp)

        if dry_run:
            out = f"[DRY_RUN] {fp.name}\n" + src
            status = "dry_run"
        else:
            out = call_openai_gpt52_translate(src, system_prompt=system_prompt)
            status = "translated"

        _write_text(out_fp, out)
        report["items"].append({"chunk_file": fp.name, "status": status, "output_path": str(out_fp)})

    report["finished_at"] = _utc_now()
    _dump_json(out_dir / "translate_run_report.json", report)
    return report

def merge_translated_chunks(
    book: str,
    target_lang: str,
    translated_root: Path,
    out_path: Path,
    file_glob: str = DEFAULT_FILE_GLOB,
) -> Dict:
    in_dir = translated_root / book / target_lang
    files = _sorted_chunk_files(in_dir, file_glob)
    if not files:
        raise RuntimeError(f"No translated chunks found: {in_dir}/{file_glob}")

    parts = []
    for fp in files:
        parts.append(_read_text(fp).rstrip() + "\n")

    merged = "\n".join(parts).rstrip() + "\n"
    _ensure_dir(out_path.parent)
    _write_text(out_path, merged)

    stamp = {
        "schema": "gaiden_merge_stamp_v1",
        "book": book,
        "target_lang": target_lang,
        "merged_at": _utc_now(),
        "chunk_count": len(files),
        "first": files[0].name,
        "last": files[-1].name,
        "output_path": str(out_path),
    }
    _dump_json(Path(str(out_path) + ".STAMP.json"), stamp)
    return stamp
