from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from gaiden.openai_client import call_agent_text, openai_healthcheck


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _chunk_paths(chunk_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(chunk_dir.glob("*.txt"))
        if path.name != "merged.txt" and not path.name.startswith("merged_")
    ]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_outputs(out_dir: Path, chunk_names: list[str], merge_name: str) -> Path:
    parts: list[str] = []
    missing: list[str] = []
    for name in chunk_names:
        path = out_dir / name
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            missing.append(name)
            continue
        parts.append(path.read_text(encoding="utf-8").rstrip())

    if missing:
        raise RuntimeError(
            "AGENT_REFINE_INCOMPLETE: missing output chunk(s): " + ", ".join(missing[:10])
        )

    merge_path = out_dir / merge_name
    merge_path.write_text("\n\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return merge_path


def run_aldebaran_refine_return(
    *,
    chunk_dir: str | Path,
    out_dir: str | Path,
    merge_name: str = "merge_refine_en.txt",
    agent_name: str = "Alamaguederaz",
    temperature: float = 0.2,
    max_output_tokens: int = 2000,
    system_prompt: str | None = None,
) -> dict[str, object]:
    """
    Send refine chunks directly to the configured agent.

    This intentionally does not read or create JSON contracts. Existing chunk
    outputs are preserved, so an interrupted run can resume at the next file.
    """
    chunk_dir = Path(chunk_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok, err = openai_healthcheck()
    if not ok:
        raise RuntimeError(err or "OPENAI_PREFLIGHT_FAILED")

    chunks = _chunk_paths(chunk_dir)
    if not chunks:
        raise RuntimeError(f"NO_REFINE_CHUNKS: nothing matched {chunk_dir}/*.txt")

    report_path = out_dir / "agent_refine_return_report.json"
    report: dict[str, Any] = {
        "schema": "agent_refine_return_v1",
        "ts_start": _now_iso(),
        "agent_name": agent_name,
        "chunk_dir": str(chunk_dir),
        "out_dir": str(out_dir),
        "merge_name": merge_name,
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
        "system_prompt": system_prompt or "",
        "count": len(chunks),
        "items": [],
        "status": "running",
    }

    for path in chunks:
        out_path = out_dir / path.name
        if out_path.exists() and out_path.read_text(encoding="utf-8").strip():
            report["items"].append(
                {"chunk": path.name, "out_txt": str(out_path), "status": "skipped_existing"}
            )
            continue

        source_text = path.read_text(encoding="utf-8")
        item: dict[str, Any] = {
            "chunk": path.name,
            "out_txt": str(out_path),
            "source_chars": len(source_text),
            "ts": _now_iso(),
            "status": "running",
        }
        try:
            refined = call_agent_text(
                agent_name=agent_name,
                text=source_text,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                system_prompt=system_prompt,
            )
            out_path.write_text(refined.rstrip() + "\n", encoding="utf-8")
            item.update(
                {
                    "status": "ok",
                    "output_chars": len(refined),
                    "length_ratio": (len(refined) / len(source_text)) if source_text else None,
                }
            )
        except Exception as exc:
            item.update({"status": "error", "error": repr(exc)})
            report["items"].append(item)
            report["status"] = "error"
            report["ts_end"] = _now_iso()
            _write_json(report_path, report)
            raise

        report["items"].append(item)
        _write_json(report_path, report)

    merge_path = _merge_outputs(out_dir, [p.name for p in chunks], merge_name)
    report["status"] = "ok"
    report["ts_end"] = _now_iso()
    report["merge_path"] = str(merge_path)
    _write_json(report_path, report)

    return {
        "agent_name": agent_name,
        "source_dir": str(chunk_dir),
        "out_dir": str(out_dir),
        "report_path": str(report_path),
        "merge_path": str(merge_path),
        "count": len(chunks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--merge-name", default="merge_refine_en.txt")
    parser.add_argument("--agent-name", default="Alamaguederaz")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-output-tokens", type=int, default=2000)
    parser.add_argument("--system-prompt-file", default="")
    args = parser.parse_args()

    system_prompt = None
    if args.system_prompt_file:
        system_prompt = Path(args.system_prompt_file).read_text(encoding="utf-8")

    result = run_aldebaran_refine_return(
        chunk_dir=args.chunk_dir,
        out_dir=args.out_dir,
        merge_name=args.merge_name,
        agent_name=args.agent_name,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        system_prompt=system_prompt,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
