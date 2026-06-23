from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


BOOK_TITLE = "Seneca’s Dialogues"
GLOSSARY_HEADING = "# Glossary"

CHAPTER_RE = re.compile(r"^Chapter\s+(\d+)$")
PART_RE = re.compile(r"^PART\s+(\d+)$", re.IGNORECASE)
BOOK_PART_RE = re.compile(r"^Book\s+PART\s+(\d+)$", re.IGNORECASE)
BOOK_RE = re.compile(r"^Book(?:\s+([IVXLCDM]+|\d+))?$", re.IGNORECASE)
RECIPIENT_RE = re.compile(r"^To\s+([A-Z][A-Za-z ]+)\.?\s*$")
ENDNOTES_RE = re.compile(r"^#?\s*(?:Endnotes|Notes|Translator.*Notes)\b", re.IGNORECASE)
ID_RE = re.compile(r'\bid="([^"]+)"')
HREF_RE = re.compile(r'\bhref="#([^"]+)"')

RESIDUE_PATTERNS = [
    "J. E. B. Mayor",
    "Koch",
    "Gertz",
    "Lipsius",
    "La Grange",
    "Mayor’s note",
    "Translator’s note",
    "See this note",
    "s.v.",
    "AUC",
    "BC",
    "Book , Chapter",
    "Aeneid , ,",
    "Livy, ,",
    "Cicero, Pro Domo , paragraph",
]

TREATISES = [
    "To Marcia, on Consolation",
    "To Helvia, on Consolation",
    "To Polybius, on Consolation",
    "On the Shortness of Life",
    "On the Happy Life",
    "On Leisure",
    "On Peace of Mind",
    "On Providence",
    "On the Firmness of the Wise Person",
    "On Anger",
    "On Benefits",
    "On Clemency",
]

ON_TREATISES = [
    "On the Shortness of Life",
    "On the Happy Life",
    "On Leisure",
    "On Peace of Mind",
    "On Providence",
    "On the Firmness of the Wise Person",
    "On Anger",
    "On Benefits",
    "On Clemency",
]


def _split_glossary(text: str) -> tuple[str, str]:
    marker = "\n# Glossary\n"
    if marker in text:
        body, rest = text.split(marker, 1)
        return body.rstrip(), "# Glossary\n" + rest
    if text.startswith("# Glossary\n"):
        return "", text
    return text.rstrip(), ""


def _extract_endnotes(body: str) -> tuple[str, str, dict[str, int | bool]]:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if ENDNOTES_RE.match(line.strip()):
            reader = "\n".join(lines[:index]).rstrip()
            raw = "\n".join(lines[index:]).rstrip() + "\n"
            return reader, raw, {
                "found": True,
                "start_line": index + 1,
                "line_count": len(lines) - index,
            }
    return body.rstrip(), "", {"found": False, "start_line": 0, "line_count": 0}


def _strip_marker_text(text: str) -> str:
    return re.sub(r"<sup\b.*?</sup>", "", text).strip()


def _match_treatise_line(line: str) -> tuple[str, str, str | None, str | None] | None:
    stripped = line.strip()
    plain = _strip_marker_text(stripped)
    for title in TREATISES:
        if plain == title:
            return title, stripped, None, None

    for title in ON_TREATISES:
        pattern = re.compile(
            rf"^({re.escape(title)}(?:\s+<sup\b.*?</sup>)?)\s+To\s+(.+?)\.?(?:\s+PART\s+(\d+))?$",
            re.IGNORECASE,
        )
        match = pattern.match(stripped)
        if match:
            heading = match.group(1).strip()
            return title, heading, f"To {match.group(2).strip().rstrip('.')}.", match.group(3)

    return None


def _is_recipient_line(line: str) -> str | None:
    match = RECIPIENT_RE.match(_strip_marker_text(line.strip()))
    if not match:
        return None
    return f"To {match.group(1).strip()}."


def _append_treatise(
    out: list[str],
    title: str,
    heading: str,
    recipient: str | None,
    line_no: int,
    treatises: list[dict[str, Any]],
) -> None:
    if out and out[-1] != "":
        out.append("")
    out.append(f"## {heading}")
    if recipient:
        out.extend(["", f"_{recipient}_"])
    out.append("")
    treatises.append({"title": title, "start_line": line_no, "chapters": 0, "parts": []})


def _current_treatise(treatises: list[dict[str, Any]]) -> dict[str, Any] | None:
    return treatises[-1] if treatises else None


def _recover_body_structure(body: str) -> tuple[str, dict[str, Any]]:
    lines = body.splitlines()
    out: list[str] = [f"# {BOOK_TITLE}", ""]
    treatises: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    chapters = 0
    parts_promoted = 0
    books_promoted = 0
    current_book = False
    current_part = False
    inserted_marcia = False
    index = 0

    while index < len(lines):
        raw = lines[index].rstrip()
        stripped = raw.strip()
        line_no = index + 1

        if not stripped:
            if out and out[-1] != "":
                out.append("")
            index += 1
            continue

        treatise_match = _match_treatise_line(stripped)
        if treatise_match:
            title, heading, recipient, inline_part = treatise_match
            next_recipient = None
            if recipient is None and index + 1 < len(lines):
                next_recipient = _is_recipient_line(lines[index + 1])
            _append_treatise(out, title, heading, recipient or next_recipient, line_no, treatises)
            current_book = False
            current_part = False
            if next_recipient:
                index += 1
            if inline_part:
                out.extend([f"### Part {inline_part}", ""])
                parts_promoted += 1
                current_part = True
                if current := _current_treatise(treatises):
                    current["parts"].append(f"Part {inline_part}")
            index += 1
            continue

        chapter_match = CHAPTER_RE.match(stripped)
        if chapter_match:
            if not inserted_marcia and not treatises:
                _append_treatise(out, "To Marcia, on Consolation", "To Marcia, on Consolation", None, line_no, treatises)
                inserted_marcia = True
            chapter_no = int(chapter_match.group(1))
            level = "##### " if current_book and current_part else "### "
            if out and out[-1] != "":
                out.append("")
            out.append(f"{level}Chapter {chapter_no}")
            out.append("")
            chapters += 1
            if current := _current_treatise(treatises):
                current["chapters"] += 1
            index += 1
            continue

        book_part_match = BOOK_PART_RE.match(stripped)
        if book_part_match:
            if out and out[-1] != "":
                out.append("")
            out.extend(["### Book", "", f"#### Part {book_part_match.group(1)}", ""])
            books_promoted += 1
            parts_promoted += 1
            current_book = True
            current_part = True
            ambiguous.append({"line": line_no, "text": stripped, "reason": "book_marker_without_number"})
            if current := _current_treatise(treatises):
                current["parts"].append(f"Part {book_part_match.group(1)}")
            index += 1
            continue

        book_match = BOOK_RE.match(stripped)
        if book_match:
            if out and out[-1] != "":
                out.append("")
            suffix = f" {book_match.group(1)}" if book_match.group(1) else ""
            out.extend([f"### Book{suffix}", ""])
            books_promoted += 1
            current_book = True
            current_part = False
            if not book_match.group(1):
                ambiguous.append({"line": line_no, "text": stripped, "reason": "book_marker_without_number"})
            index += 1
            continue

        part_match = PART_RE.match(stripped)
        if part_match:
            level = "####" if current_book else "###"
            if out and out[-1] != "":
                out.append("")
            out.extend([f"{level} Part {part_match.group(1)}", ""])
            parts_promoted += 1
            current_part = True
            if current := _current_treatise(treatises):
                current["parts"].append(f"Part {part_match.group(1)}")
            index += 1
            continue

        out.append(raw)
        index += 1

    return "\n".join(out).strip() + "\n", {
        "treatises": treatises,
        "chapters_detected": chapters,
        "parts_promoted": parts_promoted,
        "books_promoted": books_promoted,
        "ambiguous_markers": ambiguous,
    }


def _possible_residues(text: str) -> list[dict[str, Any]]:
    residues: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in RESIDUE_PATTERNS:
            if pattern in line:
                residues.append({"line": line_no, "text": line.strip(), "reason": "translator_or_editorial_residue"})
                break
    return residues


def _duplicate_items(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _validate(reader: str, glossary: str, raw_notes: str) -> dict[str, Any]:
    ids = ID_RE.findall(reader)
    hrefs = HREF_RE.findall(reader)
    id_set = set(ids)
    broken = sorted({href for href in hrefs if href not in id_set})
    plain_treatise = 0
    plain_parts = 0
    for line in reader.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("_"):
            continue
        if _match_treatise_line(stripped):
            plain_treatise += 1
        if PART_RE.match(stripped) or BOOK_PART_RE.match(stripped):
            plain_parts += 1
    return {
        "plain_treatise_titles_remaining": plain_treatise,
        "plain_part_markers_remaining": plain_parts,
        "glossary_preserved": bool(glossary and GLOSSARY_HEADING in reader),
        "duplicate_html_ids": _duplicate_items(ids),
        "broken_glossary_hrefs": broken,
        "endnotes_remaining_in_reader": bool(ENDNOTES_RE.search(reader)),
        "raw_endnotes_preserved": bool(raw_notes.strip()),
    }


def recover_seneca_structure(text: str) -> tuple[str, str, dict[str, Any]]:
    body_and_notes, glossary = _split_glossary(text)
    body, raw_notes, notes_report = _extract_endnotes(body_and_notes)
    glossary_notes_report = {"found": False, "start_line": 0, "line_count": 0}
    if glossary:
        clean_glossary, glossary_notes, glossary_notes_report = _extract_endnotes(glossary)
        if glossary_notes:
            raw_notes = (raw_notes.rstrip() + "\n\n" + glossary_notes).strip() + "\n" if raw_notes else glossary_notes
            glossary = clean_glossary.rstrip()
    structured_body, structure = _recover_body_structure(body)
    corrected = structured_body.rstrip()
    if glossary:
        corrected += "\n\n" + glossary.strip()
    corrected += "\n"
    validation = _validate(corrected, glossary, raw_notes)
    residues = _possible_residues(body)
    any_notes_found = bool(notes_report["found"] or glossary_notes_report["found"])
    passed = (
        validation["plain_treatise_titles_remaining"] == 0
        and validation["plain_part_markers_remaining"] == 0
        and validation["glossary_preserved"]
        and not validation["duplicate_html_ids"]
        and not validation["broken_glossary_hrefs"]
        and not validation["endnotes_remaining_in_reader"]
        and (not any_notes_found or validation["raw_endnotes_preserved"])
    )
    validation["passed"] = passed
    report = {
        "summary": {
            "treatises_detected": len(structure["treatises"]),
            "chapters_detected": structure["chapters_detected"],
            "parts_promoted": structure["parts_promoted"],
            "books_promoted": structure["books_promoted"],
            "plain_treatise_titles_remaining": validation["plain_treatise_titles_remaining"],
            "plain_part_markers_remaining": validation["plain_part_markers_remaining"],
            "endnotes_found": notes_report["found"] or glossary_notes_report["found"],
            "endnotes_removed_from_reader_file": (notes_report["found"] or glossary_notes_report["found"]) and not validation["endnotes_remaining_in_reader"],
            "raw_endnotes_preserved": validation["raw_endnotes_preserved"],
            "glossary_preserved": validation["glossary_preserved"],
            "glossary_links_valid": not validation["broken_glossary_hrefs"],
            "validation_passed": passed,
        },
        "treatises": structure["treatises"],
        "ambiguous_markers": structure["ambiguous_markers"],
        "possible_residues_before_endnotes": residues,
        "removed_endnotes": {
            "start_line": notes_report["start_line"] or glossary_notes_report["start_line"],
            "line_count": int(notes_report["line_count"]) + int(glossary_notes_report["line_count"]),
        },
        "validation": validation,
    }
    return corrected, raw_notes, report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Recover Seneca Dialogues structural hierarchy.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--raw-notes-output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    raw_notes_path = Path(args.raw_notes_output)
    report_path = Path(args.report)

    corrected, raw_notes, report = recover_seneca_structure(input_path.read_text(encoding="utf-8"))
    report = {
        "input": str(input_path),
        "output": str(output_path),
        "raw_endnotes_output": str(raw_notes_path),
        **report,
    }
    output_path.write_text(corrected, encoding="utf-8")
    raw_notes_path.write_text(raw_notes, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
