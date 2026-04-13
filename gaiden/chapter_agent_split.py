from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gaiden.openai_client import get_client

DEFAULT_MODEL = "gpt-5.4"
DEFAULT_FALLBACK_MODEL = "gpt-5.2"
DEFAULT_MAX_OUTPUT_TOKENS = 4000
PARTS_PER_CHAPTER = 1
MAX_PARTS_PER_CHAPTER = 4
DEFAULT_MAX_CHARS_PER_PART = 6000

_ORDINAL_WORD_MAP = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
}
_ORDINAL_WORD_RE = "|".join(sorted(_ORDINAL_WORD_MAP, key=len, reverse=True))
_CHAPTER_LINE_PATTERNS = [
    re.compile(r"^#{1,6}\s*(chapter|part|book|adventure|cap[ií]tulo|kapitel)\b.*$", re.IGNORECASE),
    re.compile(r"^(chapter|part|book|adventure|cap[ií]tulo|kapitel)\s+([ivxlcdm]+|\d+)\b.*$", re.IGNORECASE),
    re.compile(r"^#{1,6}\s*([ivxlcdm]+|\d+)[\.\):\-]\s+.+$", re.IGNORECASE),
    re.compile(r"^([ivxlcdm]+|\d+)[\.\):\-]\s+.+$", re.IGNORECASE),
    re.compile(
        rf"^#{{1,6}}\s*(?:the\s+)?({_ORDINAL_WORD_RE})\s+(chapter|part|book|adventure)\b.*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:the\s+)?({_ORDINAL_WORD_RE})\s+(chapter|part|book|adventure)\s*$",
        re.IGNORECASE,
    ),
]
_EPILOGUE_LINE_RE = re.compile(r"^epilogue\b.*$", re.IGNORECASE)
_TRAILING_SECTION_LINE_PATTERNS = [
    re.compile(r"^#{1,6}\s*(appendix|appendices|notes|endnotes|glossary|bibliography|index)\b.*$", re.IGNORECASE),
]
_ROMAN_MAP = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
    "XVII": 17,
    "XVIII": 18,
    "XIX": 19,
    "XX": 20,
}
_MAX_REASONABLE_CHAPTER_NUMBER = 50
_MIN_CHAPTER_BODY_CHARS = 800


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    return value or "chapter"


def _match_chapter_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(pattern.match(stripped) for pattern in _CHAPTER_LINE_PATTERNS)


def _parse_heading_number(line: str) -> int | None:
    stripped = line.strip().lstrip("#").strip()
    number: int | None = None
    match = re.match(
        r"^(chapter|part|book|adventure|cap[ií]tulo|kapitel)\s+([ivxlcdm]+|\d+)\b",
        stripped,
        re.IGNORECASE,
    )
    if match:
        token = match.group(2).upper()
    else:
        numbered = re.match(r"^([ivxlcdm]+|\d+)[\.\):\-]\s+.+$", stripped, re.IGNORECASE)
        if numbered:
            token = numbered.group(1).upper()
            if token.isdigit():
                number = int(token)
            else:
                number = _ROMAN_MAP.get(token)
        else:
            ordinal = re.match(
                rf"^(?:the\s+)?(?P<ordinal>{_ORDINAL_WORD_RE})\s+(chapter|part|book|adventure)\b",
                stripped,
                re.IGNORECASE,
            )
            if not ordinal:
                return None
            number = _ORDINAL_WORD_MAP.get(ordinal.group("ordinal").casefold())

    if match and token.isdigit():
        number = int(token)
    elif match:
        number = _ROMAN_MAP.get(token)
    if number is None or number < 1 or number > _MAX_REASONABLE_CHAPTER_NUMBER:
        return None
    return number


def _normalize_heading_line(line: str) -> str:
    stripped = line.strip()
    if not stripped or _EPILOGUE_LINE_RE.match(stripped):
        return stripped

    if re.match(r"^#{1,6}\s*([ivxlcdm]+|\d+)[\.\):\-]\s+.+$", stripped, re.IGNORECASE):
        return stripped
    if re.match(r"^([ivxlcdm]+|\d+)[\.\):\-]\s+.+$", stripped, re.IGNORECASE):
        return stripped

    match = re.match(
        r"^(?P<prefix>#{1,6}\s*)?(?P<label>chapter|part|book|adventure|cap[ií]tulo|kapitel)\s+"
        r"(?P<number>[ivxlcdm]+|\d+)(?P<suffix>\b.*)$",
        stripped,
        re.IGNORECASE,
    )
    if not match:
        return stripped

    number = _parse_heading_number(stripped)
    if number is None:
        return stripped

    prefix = match.group("prefix") or ""
    label = match.group("label")
    suffix = match.group("suffix") or ""
    return f"{prefix}{label.title()} {number}{suffix}".strip()


def _body_char_count(lines: list[str], start: int, end: int) -> int:
    return len("\n".join(lines[start:end]).strip())


def _epilogue_candidates(lines: list[str]) -> list[int]:
    return [idx for idx, line in enumerate(lines) if _EPILOGUE_LINE_RE.match(line.strip())]


def _trailing_nonchapter_boundary(lines: list[str], *, after_index: int) -> int | None:
    for idx in range(after_index + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped and any(pattern.match(stripped) for pattern in _TRAILING_SECTION_LINE_PATTERNS):
            return idx
    return None


def _chapter_candidates(lines: list[str]) -> list[dict[str, Any]]:
    headings = [idx for idx, line in enumerate(lines) if _match_chapter_heading(line)]
    candidates: list[dict[str, Any]] = []
    for pos, start in enumerate(headings):
        end = headings[pos + 1] if pos + 1 < len(headings) else len(lines)
        number = _parse_heading_number(lines[start])
        candidates.append(
            {
                "line_index": start,
                "end_index": end,
                "heading": lines[start].strip(),
                "number": number,
                "body_chars": _body_char_count(lines, start, end),
            }
        )
    return candidates


def _select_coherent_chapter_sequence(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    started = False
    last_number: int | None = None
    chapters_in_current_run = 0

    for item in candidates:
        number = item.get("number")
        if number is None:
            continue
        body_chars = int(item.get("body_chars") or 0)

        if not started:
            if number != 1 or body_chars < _MIN_CHAPTER_BODY_CHARS:
                continue
            started = True
            accepted.append(item)
            last_number = 1
            chapters_in_current_run = 1
            continue

        assert last_number is not None
        if number == last_number + 1:
            accepted.append(item)
            last_number = number
            chapters_in_current_run += 1
            continue

        if number == 1 and chapters_in_current_run >= 4 and body_chars >= _MIN_CHAPTER_BODY_CHARS:
            accepted.append(item)
            last_number = 1
            chapters_in_current_run = 1
            continue

    return accepted


def split_merged_text_into_chapters(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    candidates = _chapter_candidates(lines)
    accepted = _select_coherent_chapter_sequence(candidates)
    if not accepted:
        cleaned = text.strip()
        if not cleaned:
            return []
        return [
            {
                "index": 1,
                "heading": "Chapter 01",
                "slug": "chapter_01",
                "text": cleaned + "\n",
            }
        ]

    chapters: list[dict[str, Any]] = []
    boundaries = [int(item["line_index"]) for item in accepted]
    last_boundary = boundaries[-1]
    final_cutoff = _trailing_nonchapter_boundary(lines, after_index=last_boundary) or len(lines)

    epilogues = _epilogue_candidates(lines)
    trailing_epilogue = next((idx for idx in epilogues if last_boundary < idx < final_cutoff), None)
    if trailing_epilogue is not None:
        boundaries.append(trailing_epilogue)

    for chapter_index, start in enumerate(boundaries, start=1):
        end = boundaries[chapter_index] if chapter_index < len(boundaries) else final_cutoff
        chunk = "\n".join(lines[start:end]).strip()
        if not chunk:
            continue
        heading = _normalize_heading_line(lines[start])
        chunk_lines = chunk.splitlines()
        if chunk_lines:
            chunk_lines[0] = heading
            chunk = "\n".join(chunk_lines).strip()
        chapters.append(
            {
                "index": chapter_index,
                "heading": heading,
                "slug": f"{chapter_index:02d}_{_slug(heading)}",
                "text": chunk + "\n",
            }
        )
    return chapters


def _split_dense_text(text: str, parts: int) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    if parts <= 1:
        return [cleaned + "\n"]

    spans: list[str] = []
    cursor = 0
    total_len = len(cleaned)
    for idx in range(parts):
        if idx == parts - 1:
            piece = cleaned[cursor:].strip()
        else:
            target = max(cursor + ((total_len - cursor) // (parts - idx)), cursor + 1)
            while target < total_len and not cleaned[target].isspace():
                target += 1
            piece = cleaned[cursor:target].strip()
            cursor = target
        if piece:
            spans.append(piece + "\n")
    return spans


_NUMBERED_PARAGRAPH_RE = re.compile(r"^\d+\\?\.", re.IGNORECASE)


def _paragraph_is_numbered(paragraph: str) -> bool:
    first_line = paragraph.strip().splitlines()[0].strip() if paragraph.strip() else ""
    return bool(_NUMBERED_PARAGRAPH_RE.match(first_line))


def _split_paragraphs_by_numbered_boundaries(paragraphs: list[str], parts: int) -> list[list[str]] | None:
    if parts <= 1 or len(paragraphs) < parts:
        return None

    numbered_indexes = [idx for idx, item in enumerate(paragraphs) if _paragraph_is_numbered(item)]
    if len(numbered_indexes) < parts - 1:
        return None

    total_chars = sum(len(item) for item in paragraphs)
    target_chars = max(total_chars / parts, 1)
    boundaries: list[int] = []
    last_start = 0

    for cut_no in range(1, parts):
        min_start = last_start + 1
        candidates = [idx for idx in numbered_indexes if idx >= min_start]
        if not candidates:
            return None
        running = sum(len(item) for item in paragraphs[:min_start])
        desired = target_chars * cut_no
        chosen = min(candidates, key=lambda idx: abs(sum(len(item) for item in paragraphs[:idx]) - desired))
        if boundaries and chosen <= boundaries[-1]:
            later = [idx for idx in candidates if idx > boundaries[-1]]
            if not later:
                return None
            chosen = later[0]
        boundaries.append(chosen)
        last_start = chosen

    chunks: list[list[str]] = []
    start = 0
    for boundary in boundaries:
        chunk = paragraphs[start:boundary]
        if not chunk:
            return None
        chunks.append(chunk)
        start = boundary
    tail = paragraphs[start:]
    if not tail:
        return None
    chunks.append(tail)
    if len(chunks) != parts:
        return None
    return chunks


def split_chapter_into_parts(chapter_text: str, *, parts: int = PARTS_PER_CHAPTER) -> list[str]:
    cleaned = chapter_text.strip()
    if not cleaned:
        return []
    if parts < 1 or parts > MAX_PARTS_PER_CHAPTER:
        raise ValueError(f"parts deve ficar entre 1 e {MAX_PARTS_PER_CHAPTER}.")
    if parts == 1:
        return [cleaned + "\n"]

    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", cleaned) if item.strip()]
    if len(paragraphs) < parts:
        return _split_dense_text(cleaned, parts)

    numbered_split = _split_paragraphs_by_numbered_boundaries(paragraphs, parts)
    if numbered_split:
        return ["\n\n".join(bucket).strip() + "\n" for bucket in numbered_split if bucket]

    total_chars = sum(len(item) for item in paragraphs)
    target_chars = max(total_chars / parts, 1)
    out: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for idx, paragraph in enumerate(paragraphs):
        remaining_paragraphs = len(paragraphs) - idx
        remaining_slots = parts - len(out)
        if current and len(out) < parts - 1 and current_chars >= target_chars and remaining_paragraphs >= remaining_slots:
            out.append(current)
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += len(paragraph)

    if current:
        out.append(current)

    while len(out) > parts:
        tail = out.pop()
        out[-1].extend(tail)

    while len(out) < parts:
        donor_index = next((i for i, bucket in enumerate(out) if len(bucket) > 1), None)
        if donor_index is None:
            return _split_dense_text(cleaned, parts)
        donor = out[donor_index]
        moved = donor.pop()
        out.insert(donor_index + 1, [moved])

    return ["\n\n".join(bucket).strip() + "\n" for bucket in out if bucket]


def split_chapter_into_char_limited_parts(
    chapter_text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS_PER_PART,
) -> list[str]:
    cleaned = chapter_text.strip()
    if not cleaned:
        return []
    if max_chars < 1000:
        raise ValueError("max_chars deve ser pelo menos 1000.")
    content_budget = max_chars - 1

    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", cleaned) if item.strip()]
    if not paragraphs:
        return _split_dense_text(cleaned, max(1, (len(cleaned) + content_budget - 1) // content_budget))

    out: list[str] = []
    current: list[str] = []
    current_chars = 0

    for paragraph in paragraphs:
        paragraph_len = len(paragraph)
        if paragraph_len > content_budget:
            if current:
                out.append("\n\n".join(current).strip() + "\n")
                current = []
                current_chars = 0
            out.extend(_split_dense_text(paragraph, max(1, (paragraph_len + content_budget - 1) // content_budget)))
            continue

        projected = current_chars + paragraph_len + (2 if current else 0)
        if current and projected > content_budget:
            out.append("\n\n".join(current).strip() + "\n")
            current = [paragraph]
            current_chars = paragraph_len
        else:
            current.append(paragraph)
            current_chars = projected

    if current:
        out.append("\n\n".join(current).strip() + "\n")

    return out


def write_chapter_split_artifacts(
    merged_text: str,
    output_dir: Path,
    *,
    manifest_path: Path | None = None,
    parts_per_chapter: int = PARTS_PER_CHAPTER,
    max_chars_per_part: int | None = None,
) -> dict[str, Any]:
    chapters = split_merged_text_into_chapters(merged_text)
    if not chapters:
        raise ValueError("merge_translate vazio ou sem conteudo processavel.")

    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "parts_per_chapter": parts_per_chapter,
        "max_chars_per_part": max_chars_per_part,
        "chapter_count": len(chapters),
        "chapters": [],
    }

    for chapter in chapters:
        if max_chars_per_part:
            chapter_parts = split_chapter_into_char_limited_parts(
                chapter["text"],
                max_chars=max_chars_per_part,
            )
        else:
            chapter_parts = split_chapter_into_parts(chapter["text"], parts=parts_per_chapter)
        if not max_chars_per_part and len(chapter_parts) != parts_per_chapter:
            raise ValueError(
                f"Capitulo {chapter['heading']!r} nao gerou {parts_per_chapter} partes."
            )

        chapter_entry = {
            "index": chapter["index"],
            "heading": chapter["heading"],
            "slug": chapter["slug"],
            "parts": [],
        }
        for part_index, part_text in enumerate(chapter_parts, start=1):
            filename = f"chapter_{chapter['index']:02d}_part_{part_index:02d}.txt"
            part_path = output_dir / filename
            part_path.write_text(part_text, encoding="utf-8")
            chapter_entry["parts"].append(
                {
                    "index": part_index,
                    "filename": filename,
                    "char_count": len(part_text),
                }
            )
        manifest["chapters"].append(chapter_entry)

    if manifest_path is not None:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return manifest


def rewrite_single_chapter_parts(
    merged_text: str,
    output_dir: Path,
    *,
    chapter_index: int,
    parts_per_chapter: int,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    if parts_per_chapter < 1 or parts_per_chapter > MAX_PARTS_PER_CHAPTER:
        raise ValueError(f"parts_per_chapter deve ficar entre 1 e {MAX_PARTS_PER_CHAPTER}.")

    chapters = split_merged_text_into_chapters(merged_text)
    chapter = next((item for item in chapters if int(item["index"]) == int(chapter_index)), None)
    if chapter is None:
        raise ValueError(f"Capitulo {chapter_index} nao encontrado no merge_translate.")

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(output_dir.glob(f"chapter_{chapter_index:02d}_part_*.txt")):
        stale.unlink()

    chapter_parts = split_chapter_into_parts(chapter["text"], parts=parts_per_chapter)
    chapter_entry = {
        "index": chapter["index"],
        "heading": chapter["heading"],
        "slug": chapter["slug"],
        "parts": [],
    }
    for part_index, part_text in enumerate(chapter_parts, start=1):
        filename = f"chapter_{chapter['index']:02d}_part_{part_index:02d}.txt"
        part_path = output_dir / filename
        part_path.write_text(part_text, encoding="utf-8")
        chapter_entry["parts"].append(
            {
                "index": part_index,
                "filename": filename,
                "char_count": len(part_text),
            }
        )

    manifest: dict[str, Any] | None = None
    if manifest_path is not None and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        chapters_payload = manifest.get("chapters")
        if isinstance(chapters_payload, list):
            replaced = False
            for idx, existing in enumerate(chapters_payload):
                if int(existing.get("index") or 0) == int(chapter_index):
                    chapters_payload[idx] = chapter_entry
                    replaced = True
                    break
            if not replaced:
                chapters_payload.append(chapter_entry)
                chapters_payload.sort(key=lambda item: int(item.get("index") or 0))
        manifest["parts_per_chapter"] = max(
            int(manifest.get("parts_per_chapter") or 1),
            parts_per_chapter,
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "chapter_index": int(chapter["index"]),
        "heading": chapter["heading"],
        "part_count": len(chapter_parts),
        "parts": chapter_entry["parts"],
        "manifest_updated": bool(manifest_path is not None),
    }


def _chapter_agent_messages(
    text: str,
    *,
    chapter_heading: str,
    part_index: int,
    parts_total: int,
) -> list[dict[str, str]]:
    system_prompt = (
        "You are a literary revision agent. Improve clarity and fluency conservatively while preserving "
        "meaning, chronology, paragraph boundaries, names, dialogue, and chapter continuity. "
        "Return only the revised passage."
    )
    user_prompt = (
        f"Revise this excerpt from {chapter_heading}.\n"
        f"It is part {part_index} of {parts_total} for chapter-level processing after merge_translate.\n\n"
        "Rules:\n"
        "- Preserve meaning, order of events, tone, and paragraph structure.\n"
        "- Do not summarize, annotate, explain, or add headings.\n"
        "- Keep continuity with adjacent parts of the same chapter.\n"
        "- Return only the final revised excerpt.\n\n"
        f"{text}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _extract_response_text(resp: Any) -> str:
    output_text = getattr(resp, "output_text", "") or ""
    if output_text.strip():
        return output_text.strip()
    try:
        return resp.output[0].content[0].text.strip()
    except Exception as exc:
        raise RuntimeError("Resposta OpenAI sem output_text.") from exc


def run_openai_over_chapter_parts(
    manifest: dict[str, Any],
    input_dir: Path,
    output_dir: Path,
    *,
    merged_output_path: Path,
    report_path: Path | None = None,
    model: str = DEFAULT_MODEL,
    fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
    temperature: float = 0.2,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    client = get_client()
    output_dir.mkdir(parents=True, exist_ok=True)

    models = [model]
    if fallback_model and fallback_model != model:
        models.append(fallback_model)

    merged_parts: list[str] = []
    report: dict[str, Any] = {"model": model, "fallback_model": fallback_model, "items": []}

    for chapter in manifest.get("chapters", []):
        heading = str(chapter.get("heading") or f"Chapter {chapter.get('index', 0):02d}")
        parts = chapter.get("parts") or []
        for item in parts:
            filename = str(item.get("filename") or "").strip()
            if not filename:
                continue
            source_path = input_dir / filename
            source_text = source_path.read_text(encoding="utf-8").strip()
            if not source_text:
                raise ValueError(f"Arquivo vazio para agente: {source_path}")

            last_error: Exception | None = None
            response_text = ""
            used_model = model
            for current_model in models:
                try:
                    resp = client.responses.create(
                        model=current_model,
                        input=_chapter_agent_messages(
                            source_text,
                            chapter_heading=heading,
                            part_index=int(item.get("index") or 0),
                            parts_total=len(parts),
                        ),
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                    )
                    response_text = _extract_response_text(resp)
                    used_model = current_model
                    if response_text:
                        break
                except Exception as exc:
                    last_error = exc
            if not response_text:
                raise RuntimeError(
                    f"Falha ao processar {filename} via OpenAI."
                ) from last_error

            out_path = output_dir / filename
            out_path.write_text(response_text.strip() + "\n", encoding="utf-8")
            merged_parts.append(response_text.strip())
            report["items"].append(
                {
                    "chapter": heading,
                    "filename": filename,
                    "model": used_model,
                    "source_chars": len(source_text),
                    "output_chars": len(response_text),
                }
            )

    merged_text = "\n\n".join(part for part in merged_parts if part).strip() + "\n"
    merged_output_path.write_text(merged_text, encoding="utf-8")
    report["merged_output_path"] = str(merged_output_path)
    report["item_count"] = len(report["items"])
    if report_path is not None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
