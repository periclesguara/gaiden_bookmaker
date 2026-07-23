from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from . import paths


@dataclass
class QAIssue:
    issue_type: str
    description: str
    snippet: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "issue_type": self.issue_type,
            "description": self.description,
            "snippet": self.snippet,
        }


_IMAGE_REF_RE = re.compile(r"^!\[[^\]]*\]\([^)]+\)$", re.MULTILINE)


def _remove_toc_block(lines: List[str]) -> Tuple[List[str], List[QAIssue]]:
    issues: List[QAIssue] = []
    toc_start = None

    for i, line in enumerate(lines[:120]):
        if re.search(r"\b(Contents|Indice|Table of Contents)\b", line, re.IGNORECASE):
            toc_start = i
            break

    if toc_start is None:
        return lines, issues

    toc_lines = []
    end = toc_start + 1
    for j in range(toc_start + 1, min(len(lines), toc_start + 120)):
        stripped = lines[j].strip()
        if re.match(r".+\.{3,}\s*\d+$", stripped):
            toc_lines.append(lines[j])
            end = j + 1
            continue
        if stripped == "":
            end = j + 1
            continue
        break

    if toc_lines:
        issues.append(
            QAIssue(
                issue_type="toc_removed",
                description="Indice removido do inicio do livro.",
                snippet="\n".join([lines[toc_start]] + toc_lines[:5]),
            )
        )
        new_lines = lines[:toc_start] + lines[end:]
        return new_lines, issues

    return lines, issues


def _remove_foreign_publisher(text: str) -> Tuple[str, List[QAIssue]]:
    issues: List[QAIssue] = []
    patterns = [
        r"Project Gutenberg",
        r"Penguin Classics",
        r"No part of this book may be reproduced",
        r"All rights reserved",
        r"Printed in the United States",
    ]
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            issues.append(
                QAIssue(
                    issue_type="foreign_publisher",
                    description=f"Removida referencia externa: {pat}",
                    snippet=pat,
                )
            )
            text = re.sub(pat, "", text, flags=re.IGNORECASE)
    return text, issues


def run_quality_analysis(edition) -> Dict[str, object]:
    pre_path = paths.pre_qa_md_path(edition)
    if not pre_path.exists():
        raise FileNotFoundError(f"PRE_QA file not found: {pre_path}")

    md_text = pre_path.read_text(encoding="utf-8")
    lines = md_text.splitlines()
    lines, toc_issues = _remove_toc_block(lines)
    text = "\n".join(lines)
    text, pub_issues = _remove_foreign_publisher(text)

    issues = toc_issues + pub_issues

    qa_path = paths.qa_md_path(edition)
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(text, encoding="utf-8")

    log_path = paths.qa_log_path(edition)
    log_path.write_text(
        json.dumps([issue.as_dict() for issue in issues], ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    return {
        "clean_md": text,
        "issues": [issue.as_dict() for issue in issues],
        "path": str(qa_path),
        "log_path": str(log_path),
    }


def approve_md_final(edition) -> Dict[str, str]:
    qa_path = paths.qa_md_path(edition)
    pre_path = paths.pre_qa_md_path(edition)
    pre_edition_path = paths.pre_edition_md_path(edition)

    candidates = [path for path in (qa_path, pre_edition_path, pre_path) if path.exists()]
    if not candidates:
        raise FileNotFoundError("No QA, PRE_EDITION, or PRE_QA file found to approve.")

    def _image_ref_count(path) -> int:
        return len(_IMAGE_REF_RE.findall(path.read_text(encoding="utf-8")))

    source_path = candidates[0]
    if pre_edition_path.exists():
        pre_edition_images = _image_ref_count(pre_edition_path)
        selected_images = _image_ref_count(source_path)
        if pre_edition_images > selected_images:
            source_path = pre_edition_path

    from gaiden.application.pipeline import official_body

    snapshot = official_body.active_snapshot(edition)
    official_path = official_body.resolve_official_body(edition)
    if snapshot is None or official_path is None:
        raise FileNotFoundError("A valid official body is required to approve BOOK.MD_FINAL.")

    final_path = paths.final_md_path(edition)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    final_payload = final_path.read_bytes()
    manifest_path = final_path.with_name(f"{final_path.name}.source.json")
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "gaiden_final_md_derivation_v1",
                "edition_id": edition.id,
                "official_snapshot_id": snapshot.id,
                "official_sha256": snapshot.sha256,
                "final_sha256": hashlib.sha256(final_payload).hexdigest(),
                "source_filename": source_path.name,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "path": str(final_path),
        "source": str(source_path),
        "manifest": str(manifest_path),
    }
