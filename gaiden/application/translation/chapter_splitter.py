from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Callable


SPLITTER_VERSION = "gaiden_chapter_splitter_v1"
QWEN_SCHEMA = "gaiden_chapter_detection_v1"

_WORD_NUMBERS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}
_NUMBER = r"[0-9]+|[IVXLCDM]+|" + "|".join(_WORD_NUMBERS)
_CHAPTER_KINDS = {"chapter", "section", "adventure", "capítulo", "capitulo", "kapitel", "chapitre"}
_PART_KINDS = {"part", "parte", "teil", "partie"}
_BOOK_KINDS = {"book", "livro", "buch", "livre"}
_STRUCTURAL_KINDS = sorted(_CHAPTER_KINDS | _PART_KINDS | _BOOK_KINDS, key=len, reverse=True)
_STRUCTURAL_RE = re.compile(
    rf"^(?P<kind>{'|'.join(re.escape(kind) for kind in _STRUCTURAL_KINDS)})\s+"
    rf"(?P<number>{_NUMBER})(?:\s*[:.\-—–]\s*.*)?$",
    re.IGNORECASE,
)
_SPECIAL_RE = re.compile(
    r"^(?P<kind>preface|foreword|introduction|prologue|epilogue|afterword|appendix)(?:\s+[A-Z0-9IVXLCDM]+)?(?:\s*[:.\-—]\s*.+)?$",
    re.IGNORECASE,
)
_MARKDOWN_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
_STANDALONE_MARKDOWN_NUMBER_RE = re.compile(rf"^(?P<number>{_NUMBER})\.?$", re.IGNORECASE)
_ROMAN_CANONICAL_RE = re.compile(
    r"^(?=[MDCLXVI]+$)M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)
_PARAGRAPH_BOUNDARY_RE = re.compile(r"\n(?:[ \t]*\n)+")


class ChapterSplitError(ValueError):
    pass


@dataclass(frozen=True)
class SplitUnit:
    sequence: int
    unit_id: str
    unit_type: str
    heading: str
    start_offset: int
    end_offset: int
    source_sha256: str
    source_size_bytes: int
    chapter_number: str = ""
    part_number: int | None = None
    confidence: float = 1.0
    evidence: str = "deterministic heading"
    oversized: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "unit_id": self.unit_id,
            "unit_type": self.unit_type,
            "heading": self.heading,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "source_sha256": self.source_sha256,
            "source_size_bytes": self.source_size_bytes,
            "chapter_number": self.chapter_number,
            "part_number": self.part_number,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "oversized": self.oversized,
        }


@dataclass(frozen=True)
class SplitResult:
    source_sha256: str
    source_size_bytes: int
    source_characters: int
    strategy: str
    units: tuple[SplitUnit, ...]
    validated: bool
    review_required: bool
    warnings: tuple[str, ...]

    def as_manifest(self) -> dict[str, object]:
        return {
            "schema": "gaiden_chapter_split_manifest_v1",
            "splitter_version": SPLITTER_VERSION,
            "source_sha256": self.source_sha256,
            "source_size_bytes": self.source_size_bytes,
            "source_characters": self.source_characters,
            "strategy": self.strategy,
            "validated": self.validated,
            "review_required": self.review_required,
            "warnings": list(self.warnings),
            "units": [unit.as_dict() for unit in self.units],
        }


@dataclass(frozen=True)
class _Candidate:
    start: int
    line_end: int
    heading: str
    unit_type: str
    chapter_number: str
    part_number: int | None
    key: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_number(value: str) -> str:
    token = value.strip().lower()
    return _WORD_NUMBERS.get(token, value.strip().upper())


def _number_as_int(value: str) -> int | None:
    normalized = _normalize_number(value)
    if normalized.isdigit():
        return int(normalized)
    roman_values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if not normalized or any(char not in roman_values for char in normalized):
        return None
    total = 0
    previous = 0
    for char in reversed(normalized):
        current = roman_values[char]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total


def _heading_value(raw_line: str) -> str:
    value = raw_line.strip()
    markdown = _MARKDOWN_RE.match(value)
    if markdown:
        value = markdown.group(1).strip()
    return value


def _classify_heading(
    raw_line: str,
    current_part: int | None,
    *,
    special_heading_evidenced: bool = False,
) -> tuple[str, str, int | None] | None:
    heading = _heading_value(raw_line)
    if not heading or len(heading) > 500 or "[" in heading or "](" in heading:
        return None
    if re.search(r"[.·]{3,}", heading):
        return None
    structural = _STRUCTURAL_RE.fullmatch(heading)
    if structural:
        kind = structural.group("kind").lower()
        number = _normalize_number(structural.group("number"))
        if kind in _CHAPTER_KINDS:
            return "chapter", number, current_part
        if kind in _PART_KINDS:
            part = int(number) if number.isdigit() else None
            return "preliminaries", number, part
        return "preliminaries", number, current_part
    markdown = _MARKDOWN_RE.match(raw_line.strip())
    standalone = _STANDALONE_MARKDOWN_NUMBER_RE.fullmatch(heading) if markdown else None
    if standalone:
        number = _normalize_number(standalone.group("number"))
        if number.isdigit() or _ROMAN_CANONICAL_RE.fullmatch(number):
            return "chapter", number, current_part
    special = _SPECIAL_RE.fullmatch(heading)
    if not special or not special_heading_evidenced:
        return None
    kind = special.group("kind").lower()
    mapped = {
        "preface": "preface",
        "foreword": "preface",
        "introduction": "introduction",
        "prologue": "introduction",
        "epilogue": "epilogue",
        "afterword": "epilogue",
        "appendix": "appendix",
    }
    return mapped[kind], "", current_part


def _has_special_heading_evidence(lines: list[str], index: int) -> bool:
    """Require structure before treating common words as editorial headings.

    Words such as ``introduction`` and ``appendix`` can occur by themselves
    after lossy HTML-to-text conversion.  A special heading must therefore be
    either an explicit Markdown heading or a title-cased/all-caps line bounded
    by blank lines (or a document edge).
    """
    raw_line = lines[index].rstrip("\r\n")
    heading = _heading_value(raw_line)
    if not _SPECIAL_RE.fullmatch(heading):
        return False
    if _MARKDOWN_RE.match(raw_line.strip()):
        return True
    before_is_boundary = index == 0 or not lines[index - 1].strip()
    after_is_boundary = index == len(lines) - 1 or not lines[index + 1].strip()
    return (
        before_is_boundary
        and after_is_boundary
        and any(character.isupper() for character in heading)
    )


def _candidate_headings(text: str) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    offset = 0
    current_part: int | None = None
    lines = text.splitlines(keepends=True)
    for line_index, line in enumerate(lines):
        line_without_break = line.rstrip("\r\n")
        classified = _classify_heading(
            line_without_break,
            current_part,
            special_heading_evidenced=_has_special_heading_evidence(lines, line_index),
        )
        if classified:
            unit_type, chapter_number, candidate_part = classified
            heading = _heading_value(line_without_break)
            structural = _STRUCTURAL_RE.fullmatch(heading)
            if structural and structural.group("kind").lower() in _PART_KINDS:
                current_part = candidate_part
            candidate = _Candidate(
                start=offset,
                line_end=offset + len(line),
                heading=heading,
                unit_type=unit_type,
                chapter_number=chapter_number,
                part_number=current_part if unit_type == "chapter" else candidate_part,
                key=re.sub(r"\s+", " ", heading.casefold()),
            )
            candidates.append(candidate)
        offset += len(line)
    if offset < len(text):
        line = text[offset:]
        classified = _classify_heading(
            line,
            current_part,
            special_heading_evidenced=_has_special_heading_evidence([line], 0),
        )
        if classified:
            unit_type, chapter_number, candidate_part = classified
            heading = _heading_value(line)
            candidates.append(
                _Candidate(
                    start=offset,
                    line_end=len(text),
                    heading=heading,
                    unit_type=unit_type,
                    chapter_number=chapter_number,
                    part_number=candidate_part,
                    key=re.sub(r"\s+", " ", heading.casefold()),
                )
            )
    return _discard_duplicate_toc_candidates(text, candidates)


def _body_weight(text: str, candidate: _Candidate, next_start: int) -> int:
    body = text[candidate.line_end:next_start]
    body = re.sub(r"\s+", "", body)
    return len(body)


def _discard_duplicate_toc_candidates(text: str, candidates: list[_Candidate]) -> list[_Candidate]:
    by_key: dict[tuple[str, int | None], list[int]] = {}
    for index, candidate in enumerate(candidates):
        by_key.setdefault((candidate.key, candidate.part_number), []).append(index)
    rejected: set[int] = set()
    for indexes in by_key.values():
        if len(indexes) < 2:
            continue
        weighted = []
        for index in indexes:
            next_start = candidates[index + 1].start if index + 1 < len(candidates) else len(text)
            weighted.append((_body_weight(text, candidates[index], next_start), index))
        keep = max(weighted)[1]
        rejected.update(index for index in indexes if index != keep)
    return [candidate for index, candidate in enumerate(candidates) if index not in rejected]


def _make_unit(
    text: str,
    *,
    sequence: int,
    unit_type: str,
    heading: str,
    start: int,
    end: int,
    chapter_number: str = "",
    part_number: int | None = None,
    confidence: float = 1.0,
    evidence: str = "deterministic heading",
    oversized: bool = False,
) -> SplitUnit:
    if start < 0 or end <= start or end > len(text):
        raise ChapterSplitError("Offset de unidade inválido.")
    data = text[start:end].encode("utf-8")
    return SplitUnit(
        sequence=sequence,
        unit_id=f"{sequence:04d}",
        unit_type=unit_type,
        heading=heading,
        start_offset=start,
        end_offset=end,
        source_sha256=sha256_bytes(data),
        source_size_bytes=len(data),
        chapter_number=chapter_number,
        part_number=part_number,
        confidence=confidence,
        evidence=evidence,
        oversized=oversized,
    )


def _deterministic_units(text: str) -> list[SplitUnit]:
    candidates = _candidate_headings(text)
    if not candidates:
        return []
    units: list[SplitUnit] = []
    sequence = 1
    first = candidates[0]
    if first.start > 0:
        units.append(
            _make_unit(
                text,
                sequence=0,
                unit_type="preliminaries",
                heading="",
                start=0,
                end=first.start,
                evidence="content before first structural heading",
            )
        )
    for index, candidate in enumerate(candidates):
        end = candidates[index + 1].start if index + 1 < len(candidates) else len(text)
        units.append(
            _make_unit(
                text,
                sequence=sequence,
                unit_type=candidate.unit_type,
                heading=candidate.heading,
                start=candidate.start,
                end=end,
                chapter_number=candidate.chapter_number,
                part_number=candidate.part_number,
            )
        )
        sequence += 1
    return units


def _split_oversized_unit(
    text: str,
    unit: SplitUnit,
    hard_limit: int,
) -> tuple[list[SplitUnit], str | None]:
    source = text[unit.start_offset:unit.end_offset]
    if len(source) <= hard_limit:
        return [unit], None
    boundaries = [match.end() for match in _PARAGRAPH_BOUNDARY_RE.finditer(source)]
    local_start = 0
    ranges: list[tuple[int, int]] = []
    while len(source) - local_start > hard_limit:
        choices = [value for value in boundaries if local_start < value <= local_start + hard_limit]
        if not choices:
            return [replace(unit, oversized=True)], "Capítulo excede o limite e não possui fronteira segura entre parágrafos."
        local_end = max(choices)
        ranges.append((local_start, local_end))
        local_start = local_end
    ranges.append((local_start, len(source)))
    parts: list[SplitUnit] = []
    for part_number, (local_start, local_end) in enumerate(ranges, start=1):
        parts.append(
            _make_unit(
                text,
                sequence=unit.sequence,
                unit_type="oversized_chapter_part",
                heading=unit.heading,
                start=unit.start_offset + local_start,
                end=unit.start_offset + local_end,
                chapter_number=unit.chapter_number,
                part_number=part_number,
                evidence="deterministic paragraph boundary",
                oversized=True,
            )
        )
    return parts, None


def _renumber(units: list[SplitUnit]) -> list[SplitUnit]:
    result: list[SplitUnit] = []
    next_sequence = 1
    for unit in units:
        sequence = 0 if not result and unit.unit_type == "preliminaries" and unit.start_offset == 0 and not unit.heading else next_sequence
        result.append(replace(unit, sequence=sequence, unit_id=f"{sequence:04d}"))
        if sequence != 0:
            next_sequence += 1
    return result


def _qwen_units(text: str, payload: dict, confidence_threshold: float) -> tuple[list[SplitUnit], bool]:
    if payload.get("schema") != QWEN_SCHEMA or not isinstance(payload.get("units"), list):
        raise ChapterSplitError("Resposta Qwen não segue gaiden_chapter_detection_v1.")
    units: list[SplitUnit] = []
    expected_start = 0
    review_required = False
    for index, row in enumerate(payload["units"], start=1):
        if not isinstance(row, dict):
            raise ChapterSplitError("Unidade Qwen inválida.")
        start = row.get("start_offset")
        end = row.get("end_offset")
        heading = str(row.get("heading") or "").strip()
        unit_type = str(row.get("unit_type") or "chapter").strip()
        if unit_type not in {"preliminaries", "preface", "introduction", "chapter", "epilogue", "appendix"}:
            raise ChapterSplitError("Qwen sugeriu um tipo de unidade não permitido.")
        if not isinstance(start, int) or not isinstance(end, int) or start != expected_start or end <= start or end > len(text):
            raise ChapterSplitError("Offsets Qwen possuem lacuna, sobreposição ou limite inválido.")
        if unit_type == "chapter" and not heading:
            raise ChapterSplitError("Qwen sugeriu um capítulo sem heading verificável.")
        confidence = float(row.get("confidence") or 0)
        if heading and heading.casefold() not in text[start:end].casefold():
            raise ChapterSplitError("Heading Qwen não está presente na unidade sugerida.")
        if confidence < confidence_threshold:
            review_required = True
        sequence = 0 if index == 1 and unit_type == "preliminaries" else index
        units.append(
            _make_unit(
                text,
                sequence=sequence,
                unit_type=unit_type,
                heading=heading,
                start=start,
                end=end,
                confidence=confidence,
                evidence="qwen supervised offset suggestion",
            )
        )
        expected_start = end
    if expected_start != len(text):
        raise ChapterSplitError("Offsets Qwen não cobrem integralmente o fonte.")
    return units, review_required


def _chapter_sequence_warnings(units: list[SplitUnit]) -> list[str]:
    groups: dict[int | None, list[int]] = {}
    for unit in units:
        if unit.unit_type != "chapter":
            continue
        number = _number_as_int(unit.chapter_number)
        if number is not None:
            groups.setdefault(unit.part_number, []).append(number)
    warnings: list[str] = []
    for part_number, numbers in groups.items():
        if not numbers:
            continue
        if numbers[0] != 1:
            label = f"parte {part_number}" if part_number is not None else "livro"
            warnings.append(f"A numeração de capítulos em {label} não começa em 1.")
        expected = list(range(numbers[0], numbers[0] + len(numbers)))
        if numbers != expected:
            label = f"parte {part_number}" if part_number is not None else "livro"
            warnings.append(
                f"Numeração de capítulos descontínua em {label}: "
                + ", ".join(str(value) for value in numbers)
                + "."
            )
        if len(numbers) != len(set(numbers)):
            label = f"parte {part_number}" if part_number is not None else "livro"
            warnings.append(f"Numeração de capítulos duplicada em {label}.")
    return warnings


def validate_coverage(text: str, units: list[SplitUnit] | tuple[SplitUnit, ...]) -> None:
    if not units:
        raise ChapterSplitError("O split não produziu unidades.")
    expected = 0
    rebuilt: list[str] = []
    for unit in units:
        if unit.start_offset != expected:
            raise ChapterSplitError("O split possui lacuna ou sobreposição entre unidades.")
        segment = text[unit.start_offset:unit.end_offset]
        if sha256_bytes(segment.encode("utf-8")) != unit.source_sha256:
            raise ChapterSplitError("O SHA-256 de uma unidade não corresponde ao fonte.")
        rebuilt.append(segment)
        expected = unit.end_offset
    if expected != len(text) or "".join(rebuilt) != text:
        raise ChapterSplitError("A reconstrução das unidades não corresponde integralmente ao fonte.")


def split_heading_clean(
    source: bytes,
    *,
    alert_characters: int = 30_000,
    hard_limit_characters: int = 60_000,
    qwen_detector: Callable[[str], dict] | None = None,
    qwen_confidence_threshold: float = 0.85,
) -> SplitResult:
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ChapterSplitError("O artefato heading_clean precisa ser UTF-8 válido.") from exc
    if not text:
        raise ChapterSplitError("O artefato heading_clean está vazio.")
    if alert_characters <= 0 or hard_limit_characters <= alert_characters:
        raise ChapterSplitError("Limites de capítulos inválidos.")

    units = _deterministic_units(text)
    deterministic_had_chapters = any(unit.unit_type == "chapter" for unit in units)
    strategy = "deterministic"
    review_required = False
    warnings: list[str] = []
    detection_warnings = _chapter_sequence_warnings(units)
    missing_chapters = bool(units) and not deterministic_had_chapters
    if not units or detection_warnings or missing_chapters:
        if qwen_detector is None:
            if units:
                warnings.extend(detection_warnings)
                if missing_chapters:
                    warnings.append("Nenhum capítulo estrutural confiável foi encontrado.")
                review_required = True
            else:
                return SplitResult(
                    source_sha256=sha256_bytes(source),
                    source_size_bytes=len(source),
                    source_characters=len(text),
                    strategy="review_required",
                    units=(),
                    validated=False,
                    review_required=True,
                    warnings=("Nenhum heading estrutural confiável foi encontrado.",),
                )
        else:
            units, review_required = _qwen_units(text, qwen_detector(text), qwen_confidence_threshold)
            strategy = "qwen_supervised"
            if detection_warnings or missing_chapters:
                warnings.append(
                    "O splitter determinístico exigiu fallback supervisionado por inconsistência estrutural."
                )
            qwen_warnings = _chapter_sequence_warnings(units)
            if qwen_warnings:
                warnings.extend(qwen_warnings)
                review_required = True
            if not any(unit.unit_type == "chapter" for unit in units):
                warnings.append("Qwen não confirmou nenhum capítulo estrutural; revisão humana obrigatória.")
                review_required = True
    if not units:
        return SplitResult(
            source_sha256=sha256_bytes(source),
            source_size_bytes=len(source),
            source_characters=len(text),
            strategy="review_required",
            units=(),
            validated=False,
            review_required=True,
            warnings=("Nenhum heading estrutural confiável foi encontrado.",),
        )

    expanded: list[SplitUnit] = []
    for unit in units:
        character_count = unit.end_offset - unit.start_offset
        if character_count > alert_characters:
            warnings.append(f"{unit.unit_id} excede o alerta de {alert_characters} caracteres.")
        parts, warning = _split_oversized_unit(text, unit, hard_limit_characters)
        expanded.extend(parts)
        if warning:
            warnings.append(warning)
            review_required = True
    expanded = _renumber(expanded)
    validate_coverage(text, expanded)
    return SplitResult(
        source_sha256=sha256_bytes(source),
        source_size_bytes=len(source),
        source_characters=len(text),
        strategy=strategy,
        units=tuple(expanded),
        validated=not review_required,
        review_required=review_required,
        warnings=tuple(warnings),
    )
