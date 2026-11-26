from __future__ import annotations

import json
import re
from pathlib import Path

from gaiden.manuscript_artifacts import preserve_manuscript, write_once

from . import paths


def run_quality_analysis(edition) -> dict[str, object]:
    source = paths.pre_qa_md_path(edition)
    payload = source.read_bytes()
    text = payload.decode("utf-8")
    issues = []
    for pattern in (
        r"Project Gutenberg",
        r"Penguin Classics",
        r"No part of this book may be reproduced",
        r"All rights reserved",
        r"Printed in the United States",
        r"^(?:#{1,6}\s+)?(?:Contents|Indice|Índice|Table of Contents)\s*$",
    ):
        for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            issues.append({
                "issue_type": "editorial_review_required",
                "description": "Review context before changing source references or contents; text preserved.",
                "snippet": match.group(),
                "offset": match.start(),
            })
    result = preserve_manuscript(paths.qa_md_path(edition), payload)
    log_path = paths.qa_log_path(edition)
    write_once(log_path, json.dumps(issues, ensure_ascii=True, indent=2).encode("utf-8"))
    return {
        **result,
        "clean_md": text,
        "issues": issues,
        "log_path": str(log_path),
        "status": "REVIEW_REQUIRED",
        "reason": "Deterministic analysis does not certify copyediting or semantic fidelity.",
    }


def approve_manuscript(source_path: Path, final_path: Path) -> dict[str, str]:
    result = preserve_manuscript(final_path, source_path.read_bytes())
    return {**result, "source": str(source_path)}


def approve_md_final(edition) -> dict[str, str]:
    candidates = (
        paths.qa_md_path(edition),
        paths.pre_edition_md_path(edition),
        paths.pre_qa_md_path(edition),
    )
    source = next((candidate for candidate in candidates if candidate.exists()), None)
    if source is None:
        raise FileNotFoundError("No QA, PRE_EDITION, or PRE_QA file found to approve.")
    return approve_manuscript(source, paths.final_md_path(edition))
