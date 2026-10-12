from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from gaiden.application.agents.stages.modernize_en_us_2026 import _bool_arg, run_modernize_en_us_2026


def run_refine_en_us_2026(job: dict[str, Any]) -> dict[str, Any]:
    refined_job = dict(job)
    refined_job["stage"] = "refine"
    refined_job["language"] = "en_us"
    refined_job["agent_id"] = "refine_en_us_2026"
    return run_modernize_en_us_2026(refined_job)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Gaiden Refine EN-US 2026 internal agent.")
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--overwrite", type=_bool_arg, default=False)
    parser.add_argument("--job-id", default=None)
    args = parser.parse_args(argv)

    job = {
        "job_id": args.job_id or f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "book_id": args.book_id,
        "ui_stage": "refine",
        "stage": "refine",
        "language": "en_us",
        "target_language": "en_us",
        "agent_id": "refine_en_us_2026",
        "input": {"source_path": args.source},
        "output": {"target_path": args.target, "overwrite": args.overwrite},
    }
    report = run_refine_en_us_2026(job)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
