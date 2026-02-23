#!/usr/bin/env python3
import os
import sys
import json
import time
import glob
import hashlib
import argparse
from datetime import datetime
from pathlib import Path

# --- CONFIG DEFAULTS (sobrescrevivel via env/args) ---
DEFAULT_MODEL = os.getenv("GAIDEN_AGENT_MODEL", "gpt-5-chat-latest")
DEFAULT_AGENT = os.getenv("GAIDEN_AGENT_NAME", "ALAMAGUEDERAZ")


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def load_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def now_stamp() -> str:
    # compat com teu padrao (ja vi 20260218T214017)
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S")


def pick_chunks(chunks_dir: Path) -> list[Path]:
    patterns = ["*.txt", "*.md"]
    files = []
    for pat in patterns:
        files.extend(sorted(chunks_dir.glob(pat)))
    # fallback: qualquer coisa que nao seja diretorio
    if not files:
        files = sorted([p for p in chunks_dir.iterdir() if p.is_file()])
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Send text chunks to an agent with retries and merged output."
    )
    parser.add_argument("book_code")
    parser.add_argument("src_lang", nargs="?", default="en")
    parser.add_argument(
        "--chunks-dir",
        dest="chunks_dir",
        help="Explicit chunks directory. Defaults to data/chunks/<book_code>/<src_lang>.",
    )
    parser.add_argument(
        "--out-root",
        dest="out_root",
        help="Explicit output root. If set, writes to <out_root>/agent_<agent>.",
    )
    args = parser.parse_args()

    book_code = args.book_code
    src_lang = args.src_lang

    chunks_dir = Path(args.chunks_dir) if args.chunks_dir else Path(f"data/chunks/{book_code}/{src_lang}")
    if not chunks_dir.exists():
        print(f"[FATAL] chunks_dir not found: {chunks_dir}", file=sys.stderr)
        sys.exit(1)

    chunks = pick_chunks(chunks_dir)
    if not chunks:
        print(f"[FATAL] no chunk files found under: {chunks_dir}", file=sys.stderr)
        sys.exit(1)

    run_id = now_stamp()
    if args.out_root:
        out_dir = Path(args.out_root) / f"agent_{DEFAULT_AGENT.lower()}" / run_id
    else:
        out_dir = Path(f"data/books/{book_code}/{src_lang}/runs/agent_{DEFAULT_AGENT.lower()}/{run_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "book_code": book_code,
        "src_lang": src_lang,
        "agent": DEFAULT_AGENT,
        "model": DEFAULT_MODEL,
        "run_id": run_id,
        "chunks_dir": str(chunks_dir),
        "out_dir": str(out_dir),
        "started_utc": datetime.utcnow().isoformat() + "Z",
        "items": [],
        "errors": [],
    }

    # Import aqui pra falhar cedo, mas depois de validar paths
    try:
        from gaiden.openai_client import call_agent_text
    except Exception as e:
        print("[FATAL] cannot import gaiden.openai_client.call_agent_text:", repr(e), file=sys.stderr)
        sys.exit(1)

    # knobs
    max_retries = int(os.getenv("GAIDEN_AGENT_RETRIES", "4"))
    sleep_base = float(os.getenv("GAIDEN_AGENT_SLEEP_BASE", "2.0"))

    merged_parts = []
    for idx, chunk_path in enumerate(chunks, start=1):
        chunk_name = chunk_path.name
        raw = load_text(chunk_path)

        item = {
            "i": idx,
            "chunk": chunk_name,
            "bytes": len(raw.encode("utf-8", errors="ignore")),
            "sha1": sha1_text(raw),
            "ok": False,
            "attempts": 0,
            "out_file": None,
            "err": None,
        }

        # output file stable
        out_file = out_dir / "chunks_out" / f"{idx:04d}__{chunk_name}.out.txt"

        # skip if already exists (idempotencia)
        if out_file.exists() and out_file.stat().st_size > 20:
            out_text = load_text(out_file)
            item["ok"] = True
            item["out_file"] = str(out_file)
            report["items"].append(item)
            merged_parts.append(out_text.rstrip() + "\n")
            print(f"[SKIP] {idx:04d} {chunk_name} (already exists)")
            continue

        # call agent with retry
        last_err = None
        for attempt in range(1, max_retries + 1):
            item["attempts"] = attempt
            try:
                # IMPORTANTE: agente tem prompt interno; mandamos so o texto
                out_text = call_agent_text(
                    agent_name=DEFAULT_AGENT,
                    text=raw,
                    model=DEFAULT_MODEL,
                    temperature=0.2,
                    max_output_tokens=8000,
                )
                if not isinstance(out_text, str) or len(out_text.strip()) < 10:
                    raise RuntimeError(
                        f"empty/short output (len={len(out_text) if isinstance(out_text, str) else 'NA'})"
                    )

                write_text(out_file, out_text)
                item["ok"] = True
                item["out_file"] = str(out_file)
                merged_parts.append(out_text.rstrip() + "\n")
                print(f"[OK]   {idx:04d} {chunk_name} -> {out_file}")
                last_err = None
                break

            except Exception as e:
                last_err = repr(e)
                item["err"] = last_err
                # backoff simples
                delay = sleep_base * (attempt ** 1.5)
                print(f"[ERR]  {idx:04d} {chunk_name} attempt={attempt}/{max_retries} err={last_err}")
                time.sleep(delay)

        if last_err is not None and not item["ok"]:
            report["errors"].append(item)
            report["items"].append(item)
            # nao explode aqui: continua e entrega lista do que falhou
            continue

        report["items"].append(item)

    merged_path = out_dir / f"{book_code}__{src_lang}__agent_{DEFAULT_AGENT.lower()}__MERGED.txt"
    write_text(merged_path, "\n".join(merged_parts).rstrip() + "\n")

    report["finished_utc"] = datetime.utcnow().isoformat() + "Z"
    report["merged_path"] = str(merged_path)
    report_path = out_dir / "report.json"
    write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False))

    print("\n=== DONE ===")
    print(f"merged: {merged_path}")
    print(f"report: {report_path}")
    if report["errors"]:
        print(f"FAILURES: {len(report['errors'])}")
        for it in report["errors"][:20]:
            print(f" - {it['i']:04d} {it['chunk']}: {it.get('err')}")
        sys.exit(3)


if __name__ == "__main__":
    main()
