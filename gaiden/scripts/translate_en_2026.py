#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any, List

# ---- paths ----
REPO_ROOT = Path(__file__).resolve().parents[2]  # adjust if your scripts folder differs
DATA_DIR = REPO_ROOT / "data"
CHUNKS_DIR = DATA_DIR / "chunks"
TRANSLATED_DIR = DATA_DIR / "translated"
CONTRACTS_DIR = REPO_ROOT / "gaiden" / "contracts"

# Ensure repo root is on sys.path when running as a script
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---- utils ----
def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def write_failed_output(out_dir_path: Path, chunk_file: str, out_text: str, reason: str) -> None:
    failed_dir = out_dir_path / "FAILED"
    failed_dir.mkdir(parents=True, exist_ok=True)
    base = chunk_file.replace(".txt", "")
    write_text(failed_dir / f"{base}.FAILED.txt", out_text.strip() + "\n")
    write_text(failed_dir / f"{base}.reason.txt", reason.strip() + "\n")


def count_paragraphs(s: str) -> int:
    # paragraph breaks are double-newlines conventionally
    parts = [x for x in s.split("\n\n") if x.strip() != ""]
    return max(1, len(parts)) if s.strip() else 0


def length_ratio(inp: str, out: str) -> float:
    return len(out) / max(1, len(inp))


def load_contract(contract_path: Path) -> Dict[str, Any]:
    return json.loads(read_text(contract_path))


def render_user_prompt(template: str, text: str) -> str:
    # Keep placeholder stable. If your project uses a different token, change here.
    return template.replace("{{TEXT}}", text)


# ---- OpenAI client hookup (project-specific) ----
def call_openai(contract: Dict[str, Any], input_text: str) -> str:
    """
    This is intentionally minimal and project-aligned:
    - Uses .gaiden_secrets via your existing openai_client.py.
    """
    from gaiden.openai_client import get_client

    model = contract.get("model", "gpt-5.2")
    temperature = contract.get("temperature", 0.3)
    max_output_tokens = contract.get("max_output_tokens", 2400)
    system_prompt = contract["system_prompt"]
    user_prompt = render_user_prompt(contract["user_prompt"], input_text)

    client = get_client()
    messages = [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt},
    ]

    resp = client.responses.create(
        model=model,
        input=messages,
        temperature=float(temperature),
        max_output_tokens=int(max_output_tokens),
    )

    # Prefer full output_text when available; otherwise join all text parts.
    try:
        out = getattr(resp, "output_text", None)
        if out:
            return out.strip()
    except Exception:
        pass

    parts: List[str] = []
    try:
        for out_item in resp.output:
            for c in getattr(out_item, "content", []) or []:
                text = getattr(c, "text", None)
                if text:
                    parts.append(text)
    except Exception:
        parts = []

    if parts:
        return "".join(parts).strip()

    raise RuntimeError("Could not extract text from model response.")


def load_manifest(book_code: str) -> Dict[str, Any]:
    manifest_path = CHUNKS_DIR / book_code / "en" / "chunks_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing chunks manifest: {manifest_path}")
    return json.loads(read_text(manifest_path))


def chunk_input_path(book_code: str, chunk_file: str) -> Path:
    # chunk_file is like ch_500_chunk_001.txt
    return CHUNKS_DIR / book_code / "en" / chunk_file


def out_dir(book_code: str) -> Path:
    # canonical output dir for this 2026 modernization
    return TRANSLATED_DIR / book_code / "en_2026"


def out_chunk_name(chunk_file: str) -> str:
    # ch_500_chunk_001.txt -> ch_500_chunk_001.EN_2026.txt
    base = chunk_file.replace(".txt", "")
    return f"{base}.EN_2026.txt"


def merge_output_path(book_code: str) -> Path:
    return out_dir(book_code) / "merge_translate_EN_2026.txt"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, help="book code, e.g. book_0003")
    ap.add_argument(
        "--contract", default="en_modern_2026.json", help="contract file in gaiden/contracts/"
    )
    ap.add_argument("--limit", type=int, default=0, help="process only first N chunks (0 = all)")
    ap.add_argument("--overwrite", action="store_true", help="overwrite existing outputs")
    args = ap.parse_args()

    book_code = args.book.strip()
    contract_path = CONTRACTS_DIR / args.contract
    if not contract_path.exists():
        raise FileNotFoundError(f"Contract not found: {contract_path}")

    contract = load_contract(contract_path)
    manifest = load_manifest(book_code)

    # Determine chunk list in manifest order (avoid filesystem glob ordering bugs)
    chapters = manifest.get("per_chapter", [])
    if not chapters:
        raise RuntimeError("Manifest missing per_chapter list.")

    # Flatten chunk files in order
    chunk_files: List[str] = []
    for ch in chapters:
        for cf in ch.get("chunk_files", []):
            chunk_files.append(cf)

    if args.limit and args.limit > 0:
        chunk_files = chunk_files[: args.limit]

    odir = out_dir(book_code)
    odir.mkdir(parents=True, exist_ok=True)

    merged_parts: List[str] = []
    processed = 0

    # Guards configuration
    MIN_RATIO = 0.85
    MIN_INPUT_CHARS_FOR_RATIO = 2000

    for cf in chunk_files:
        inp_path = chunk_input_path(book_code, cf)
        if not inp_path.exists():
            raise FileNotFoundError(f"Missing chunk input: {inp_path}")

        inp_text = read_text(inp_path)
        out_path = odir / out_chunk_name(cf)

        if out_path.exists() and not args.overwrite:
            # reuse existing output
            out_text = read_text(out_path)
            merged_parts.append(out_text.rstrip() + "\n")
            processed += 1
            continue

        # Call model (expensive) — FAIL FAST policy
        out_text = call_openai(contract, inp_text)

        # Guards
        r = length_ratio(inp_text, out_text)
        paras_in = count_paragraphs(inp_text)
        paras_out = count_paragraphs(out_text)

        # Log (simple stdout)
        print(
            f"[{book_code}] {cf} model={contract.get('model','gpt-5.2')} ratio={r:.3f} paras_in={paras_in} paras_out={paras_out}"
        )

        if len(inp_text) >= MIN_INPUT_CHARS_FOR_RATIO and r < MIN_RATIO:
            reason = f"COMPRESSION_FAIL: {cf} ratio={r:.3f} (min {MIN_RATIO})"
            write_failed_output(odir, cf, out_text, reason)
            raise RuntimeError(reason)

        if paras_out != paras_in:
            reason = f"PARAGRAPH_MISMATCH: {cf} paras_in={paras_in} paras_out={paras_out}"
            write_failed_output(odir, cf, out_text, reason)
            raise RuntimeError(reason)

        write_text(out_path, out_text.strip() + "\n")
        merged_parts.append(out_text.strip() + "\n")
        processed += 1

    # Merge output
    merge_path = merge_output_path(book_code)
    write_text(merge_path, "\n\n".join([p.strip() for p in merged_parts if p.strip()]) + "\n")
    print(f"OK: wrote merge -> {merge_path}")
    print(f"OK: processed chunks -> {processed}")


if __name__ == "__main__":
    main()
