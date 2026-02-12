from __future__ import annotations

import argparse
import json
from pathlib import Path

from gaiden.openai_client import call_agent_text

ROOT = Path(__file__).resolve().parents[1]


def _p(s: str) -> Path:
    p = Path(s)
    return p if p.is_absolute() else (ROOT / p).resolve()


def load_contract(path: str) -> dict:
    cp = _p(path)
    if not cp.exists():
        raise FileNotFoundError(f"Contrato não encontrado: {cp}")
    return json.loads(cp.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Send splits to Agent/Flow and merge outputs.")
    ap.add_argument("contract", help="path to gaiden/contracts_v2/refine/*.json")
    ap.add_argument("--limit", type=int, default=0, help="process only N files (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="do not call API; just show planned actions")
    args = ap.parse_args()

    cfg = load_contract(args.contract)

    agent = cfg["flow_name"]  # aldebaran / kaiser / el_obregon
    model = cfg.get("model", "gpt-5-chat-latest")

    splits_dir = _p(cfg["splits_dir"])
    out_dir = _p(cfg["out_dir"])

    file_glob = cfg.get("file_glob", "*.txt")
    out_suffix = cfg.get("out_suffix", ".RETURN.txt")
    merge_name = cfg.get("merge_name", "merge_refine.txt")
    system_prompt = cfg.get("system_prompt")
    user_template = cfg.get("user_prompt", "{text}")

    temperature = cfg.get("temperature", 0.2)
    max_output_tokens = cfg.get("max_output_tokens", 6500)

    if not splits_dir.exists():
        raise FileNotFoundError(f"splits_dir não existe: {splits_dir}")

    files = sorted([p for p in splits_dir.glob(file_glob) if p.is_file()])
    if not files:
        raise RuntimeError(f"Nenhum split encontrado em {splits_dir}")

    if args.limit and args.limit > 0:
        files = files[: args.limit]

    out_dir.mkdir(parents=True, exist_ok=True)

    merged = []

    print(f"[CFG] agent={agent}")
    print(f"[CFG] model={model}")
    print(f"[CFG] splits_dir={splits_dir}")
    print(f"[CFG] out_dir={out_dir}")
    print(f"[CFG] files={len(files)}")

    for i, f in enumerate(files, start=1):
        out_file = out_dir / f"{f.stem}{out_suffix}"

        if args.dry_run:
            print(f"[DRY] {i:02d}/{len(files)} {f.name} -> {out_file.name}")
            merged.append(f"[DRY_OUTPUT] {f.stem}")
            continue

        text = f.read_text(encoding="utf-8", errors="ignore")
        user_text = user_template.replace("{text}", text)

        result = call_agent_text(
            agent_name=agent,
            text=user_text,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_prompt=system_prompt,
        )

        out_file.write_text(result, encoding="utf-8")
        merged.append(result.strip())

        print(f"[OK] {i:02d}/{len(files)} {f.name}")

    merge_path = out_dir / merge_name
    merge_path.write_text("\n\n".join(merged).strip() + "\n", encoding="utf-8")
    print(f"[DONE] merge -> {merge_path}")


if __name__ == "__main__":
    main()
