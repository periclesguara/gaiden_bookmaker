from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import pathlib
from pathlib import Path
from typing import Dict, Any, List, Tuple

from gaiden.openai_client import call_agent_text
from gaiden.secrets_loader import get_openai_config

CHUNK_IN_RE = re.compile(r"^ch_(\d{2})_part_(\d{2})\.txt$")
ALDEBARAN_RE = re.compile(r"^ch_(\d{2})_part_(\d{2})\.ALDEBARAN\.txt$")
YODA_RE = re.compile(r"^ch_(\d{2})_part_(\d{2})\.YODA\.txt$")


def _load_contract(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Contrato não encontrado: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_book_placeholder(value: str, book: str | None) -> str:
    if "{BOOK_ID}" not in value:
        return value
    if not book:
        raise SystemExit(
            "Contrato usa {BOOK_ID}. Use --book para resolver o placeholder."
        )
    return value.replace("{BOOK_ID}", book)


def _build_messages(chunk_text: str, contract: Dict[str, Any]) -> Tuple[str, str]:
    system_prompt = contract.get("system_prompt") or contract.get("system") or ""
    user_template = contract.get("user_prompt") or contract.get("user") or "{text}"
    user_text = user_template.replace("{text}", chunk_text)
    return system_prompt.strip(), user_text


def _validate_input_dir(path: Path):
    if not path.is_dir():
        raise FileNotFoundError(f"Diretório não encontrado: {path}")
    if "split_chapters_for_refine" not in str(path):
        raise RuntimeError(
            f"chunk_dir inválido (esperado split_chapters_for_refine): {path}"
        )


def _list_input_files(path: Path) -> List[Path]:
    txt_files = sorted([p for p in path.iterdir() if p.is_file() and p.suffix == ".txt"])
    for p in txt_files:
        if not CHUNK_IN_RE.match(p.name):
            raise RuntimeError(f"Arquivo de entrada inválido: {p.name}")
    if not txt_files:
        raise RuntimeError(f"Nenhum chunk encontrado em {path}")
    return txt_files


def _list_aldebaran_files(path: Path) -> List[Path]:
    files = sorted([p for p in path.iterdir() if p.is_file() and p.suffix == ".txt"])
    for p in files:
        if not ALDEBARAN_RE.match(p.name):
            raise RuntimeError(f"Arquivo Aldebaran inválido: {p.name}")
    return files


def _list_yoda_files(path: Path) -> List[Path]:
    files = sorted([p for p in path.iterdir() if p.is_file() and p.suffix == ".txt"])
    for p in files:
        if not YODA_RE.match(p.name):
            raise RuntimeError(f"Arquivo Yoda inválido: {p.name}")
    return files


def _run_stage(
    *,
    stage_name: str,
    agent_name: str,
    contract: Dict[str, Any],
    input_files: List[Path],
    out_dir: Path,
    suffix: str,
    dry_run: bool,
    limit_chunks: int | None = None,
) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_files: List[Path] = []

    model = contract.get("model")
    temperature = contract.get("temperature")
    max_output_tokens = contract.get("max_output_tokens")

    files = input_files[:limit_chunks] if limit_chunks else input_files
    for p in files:
        chunk_text = p.read_text(encoding="utf-8").rstrip()
        system_prompt, user_text = _build_messages(chunk_text, contract)
        if dry_run:
            refined = f"[DRY-RUN] {chunk_text[:2000]}"
        else:
            refined = call_agent_text(
                agent_name=agent_name,
                text=user_text,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )

        if suffix == "YODA" and p.name.endswith(".ALDEBARAN.txt"):
            out_name = p.name.replace(".ALDEBARAN.txt", ".YODA.txt")
        else:
            out_name = p.name.replace(".txt", f".{suffix}.txt")
        out_path = out_dir / out_name
        out_path.write_text(refined.strip() + "\n", encoding="utf-8")
        out_files.append(out_path)

    if not out_files:
        raise RuntimeError(f"Nenhum arquivo gerado na etapa {stage_name}")
    return out_files


def _merge_yoda_outputs(yoda_dir: Path, out_path: Path) -> Path:
    yoda_files = _list_yoda_files(yoda_dir)
    if not yoda_files:
        raise RuntimeError("Diretório Yoda vazio antes do merge")

    parts: List[str] = []
    for p in yoda_files:
        text = p.read_text(encoding="utf-8").strip()
        if text:
            parts.append(text)

    merged = "\n\n".join(parts) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(merged, encoding="utf-8")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("aldebaran_contract")
    ap.add_argument("yoda_contract")
    ap.add_argument("--book", required=True)
    ap.add_argument("--dry-run", action="store_true", help="Não chama API; escreve outputs fake para testar pipeline")
    ap.add_argument("--limit-chunks", type=int, default=None)
    args = ap.parse_args()

    # Preflight: load config, set env, and verify DNS/connectivity (skip on dry-run)
    cfg = get_openai_config()
    os.environ["OPENAI_API_KEY"] = cfg["OPENAI_API_KEY"]
    os.environ["OPENAI_BASE_URL"] = cfg.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"]:
        if cfg.get(k):
            os.environ[k] = cfg[k]

    if args.dry_run:
        print("[NET] dry-run: skipping DNS/httpx checks")
    else:
        print("[NET] base_url:", os.environ.get("OPENAI_BASE_URL"))
        print(
            "[NET] key_present:",
            bool(os.environ.get("OPENAI_API_KEY")),
            "len:",
            len(os.environ.get("OPENAI_API_KEY") or ""),
        )

        print("[NET] cwd:", pathlib.Path().resolve())
        print("[NET] sys.executable:", sys.executable)
        print("[NET] venv:", sys.prefix)
        print("[NET] /etc/resolv.conf (head):")
        try:
            print(pathlib.Path("/etc/resolv.conf").read_text().splitlines()[:20])
        except Exception as e:
            print("FAIL read resolv.conf:", repr(e))

        print("[NET] resolvectl status (head):")
        try:
            out = subprocess.check_output(
                ["resolvectl", "status"],
                stderr=subprocess.STDOUT,
                text=True,
            )
            print(out.splitlines()[:80])
        except Exception as e:
            print("FAIL resolvectl:", repr(e))

        print("[NET] getent hosts api.openai.com:")
        try:
            out = subprocess.check_output(
                ["getent", "hosts", "api.openai.com"],
                stderr=subprocess.STDOUT,
                text=True,
            )
            print(out.strip())
        except Exception as e:
            print("FAIL getent:", repr(e))

        host = "api.openai.com"
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            print("[NET] DNS OK:", host, "->", infos[0][4][0])
        except Exception as e:
            print("[NET] DNS FAIL:", host, repr(e))
            raise

        try:
            import httpx

            r = httpx.get("https://api.openai.com/v1/models", timeout=10.0)
            print("[NET] httpx GET /models:", r.status_code)
        except Exception as e:
            print("[NET] httpx FAIL:", repr(e))
            raise

    book = args.book
    aldebaran = _load_contract(args.aldebaran_contract)
    yoda = _load_contract(args.yoda_contract)

    chunk_dir = _resolve_book_placeholder(aldebaran.get("chunk_dir", ""), book)
    out_dir_a = _resolve_book_placeholder(aldebaran.get("out_dir", ""), book)
    out_dir_y = _resolve_book_placeholder(yoda.get("out_dir", ""), book)
    yoda_in_dir = _resolve_book_placeholder(yoda.get("chunk_dir", ""), book)

    if not chunk_dir or not out_dir_a or not out_dir_y or not yoda_in_dir:
        raise RuntimeError("Contrato inválido: chunk_dir/out_dir ausentes")

    chunk_dir_path = Path(chunk_dir)
    _validate_input_dir(chunk_dir_path)
    input_files = _list_input_files(chunk_dir_path)

    # Stage A: Aldebaran
    _run_stage(
        stage_name="aldebaran",
        agent_name="ALDEBARAN",
        contract=aldebaran,
        input_files=input_files,
        out_dir=Path(out_dir_a),
        suffix="ALDEBARAN",
        dry_run=args.dry_run,
        limit_chunks=args.limit_chunks,
    )

    # Stage B: Yoda
    yoda_in_path = Path(yoda_in_dir)
    if not yoda_in_path.is_dir():
        raise RuntimeError(f"Diretório Aldebaran não encontrado: {yoda_in_path}")
    al_files = _list_aldebaran_files(yoda_in_path)
    if not al_files:
        raise RuntimeError("Diretório Aldebaran vazio antes do Yoda")

    _run_stage(
        stage_name="yoda",
        agent_name="YODA_MING",
        contract=yoda,
        input_files=al_files,
        out_dir=Path(out_dir_y),
        suffix="YODA",
        dry_run=args.dry_run,
        limit_chunks=None,
    )

    # Merge
    merge_path = Path(f"data/builds/{book}/en/return/merge_refine_en.txt")
    _merge_yoda_outputs(Path(out_dir_y), merge_path)

    print(f"[OK] return_en completed: {merge_path}")


if __name__ == "__main__":
    main()
