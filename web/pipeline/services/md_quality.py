from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

from . import edition_meta, paths


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


def _legacy_pre_qa_candidates(edition, build_dir: Path, language: str) -> list[Path]:
    return [
        build_dir / f"BOOK.PRE_QA.{language}.md",
        build_dir / "BOOK.PRE_QA.md",
    ]


def _legacy_qa_candidates(edition, build_dir: Path, language: str) -> list[Path]:
    return [
        build_dir / f"BOOK.QA.{language}.md",
        build_dir / "BOOK.QA.md",
    ]


def _legacy_pre_edition_candidates(edition, build_dir: Path, language: str) -> list[Path]:
    return [
        build_dir / f"BOOK.PRE_EDITION.{language}.md",
        build_dir / "BOOK.PRE_EDITION.md",
    ]


def run_quality_analysis(
    edition,
    language_override: str | None = None,
    version_override: str | None = None,
) -> Dict[str, object]:
    lang = language_override or edition_meta.language_code(edition)
    build_dir = paths.edition_build_dir_for_language(edition_meta.book_code(edition), lang)
    pre_path = paths.pre_qa_md_path(edition, language=lang, version=version_override)
    if not pre_path.exists():
        pre_path = next(
            (p for p in _legacy_pre_qa_candidates(edition, build_dir, lang) if p.exists()),
            None,
        )
    if not pre_path:
        raise FileNotFoundError("PRE_QA file not found.")

    md_text = pre_path.read_text(encoding="utf-8")
    lines = md_text.splitlines()
    lines, toc_issues = _remove_toc_block(lines)
    text = "\n".join(lines)
    text, pub_issues = _remove_foreign_publisher(text)

    issues = toc_issues + pub_issues

    qa_path = paths.qa_md_path(edition, language=lang, version=version_override)
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


def approve_md_final(
    edition,
    language_override: str | None = None,
    version_override: str | None = None,
) -> Dict[str, str]:
    lang = language_override or edition_meta.language_code(edition)
    build_dir = paths.edition_build_dir_for_language(edition_meta.book_code(edition), lang)
    qa_path = paths.qa_md_path(edition, language=lang, version=version_override)
    pre_path = paths.pre_qa_md_path(edition, language=lang, version=version_override)
    pre_edition_path = paths.pre_edition_md_path(edition, language=lang, version=version_override)
    if qa_path.exists():
        source_path = qa_path
    elif pre_edition_path.exists():
        source_path = pre_edition_path
    elif pre_path.exists():
        source_path = pre_path
    else:
        legacy_candidates = (
            _legacy_qa_candidates(edition, build_dir, lang)
            + _legacy_pre_edition_candidates(edition, build_dir, lang)
            + _legacy_pre_qa_candidates(edition, build_dir, lang)
        )
        source_path = next((p for p in legacy_candidates if p.exists()), None)
        if not source_path:
            raise FileNotFoundError("No QA, PRE_EDITION, or PRE_QA file found to approve.")

    final_path = paths.final_md_path(edition, language=lang, version=version_override)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    return {
        "path": str(final_path),
        "source": str(source_path),
    }
