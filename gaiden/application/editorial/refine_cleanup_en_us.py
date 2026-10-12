from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STAGE_NAME = "refine_cleanup_en_us"
EXPECTED_BOOK_ORDER = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
DEFAULT_REPLACEMENTS_CONFIG = Path("data/config/refine_cleanup_en_us_replacements.json")

GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]+(?:[ \t]+[\u0370-\u03ff\u1f00-\u1fff]+)*")
BOOK_RE = re.compile(r"^\s*(?:#{1,6}\s*)?BOOK\s+([IVXLCDM]+|\d+)\b.*$", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^\s*(?:#{1,6}\s*)?Chapter\s+([IVXLCDM]+|\d+)\b.*$", re.IGNORECASE)
GLOSSARY_MARKER_RE = re.compile(r"\[([GNPC]\d{2})\]")

GREEK_TRANSLATION_MAP: dict[str, dict[str, str | None]] = {
    "πολιτικὴ": {
        "english": "political science",
        "alternate": "statesmanship",
        "category": "greek_term",
        "transliteration": "politikē",
        "note": "The practical master science concerned with human good in the community.",
    },
    "ἰδέα": {
        "english": "Form",
        "alternate": "Idea",
        "category": "greek_term",
        "transliteration": "idea",
        "note": "Used in the Platonic sense of a separable Form or Idea.",
    },
    "εἴδη": {
        "english": "Forms",
        "alternate": "kinds",
        "category": "greek_term",
        "transliteration": "eidē",
        "note": "Usually Platonic Forms in this context; use 'kinds' only when the context is classificatory rather than Platonic.",
    },
    "λόγος": {
        "english": "reason",
        "alternate": "rational account; principle",
        "category": "greek_term",
        "transliteration": "logos",
        "note": "Translate according to context: reason, account, or principle.",
    },
    "καλὸν": {
        "english": "the noble",
        "alternate": "the fine; the honorable",
        "category": "greek_term",
        "transliteration": "kalon",
        "note": "A central Greek ethical term combining nobility, beauty, and moral honor.",
    },
    "ἐν βίῳ τελείῳ": {
        "english": "in a complete life",
        "alternate": "over a complete life",
        "category": "greek_phrase",
        "transliteration": "en bio teleio",
        "note": "Important phrase in Aristotle's account of happiness as requiring a complete life.",
    },
    "χαυνότης": {
        "english": "vanity",
        "alternate": "empty pride",
        "category": "greek_term",
        "transliteration": "chaunotēs",
        "note": "The excessive state opposed to proper greatness of soul.",
    },
    "δίκαιον": {
        "english": "the just",
        "alternate": "the just thing",
        "category": "greek_term",
        "transliteration": "dikaion",
        "note": "May refer to justice in the abstract or to the just action/object depending on context.",
    },
    "χάρις": {
        "english": "favor",
        "alternate": "grace; gratitude",
        "category": "greek_term",
        "transliteration": "charis",
        "note": "Use according to context: favor, gratitude, or grace.",
    },
    "γνώμη": {
        "english": "judgment",
        "alternate": "discernment",
        "category": "greek_term",
        "transliteration": "gnōmē",
        "note": "Practical judgment or discernment.",
    },
    "κλεὶς": {"english": "key", "alternate": None, "category": "greek_term", "transliteration": "kleis", "note": "Used as an example of equivocal naming, meaning either a clavicle or a door key."},
    "μεσίδιοι": {"english": "middle-men", "alternate": "mediators", "category": "greek_term", "transliteration": "mesidioi", "note": "A Greek term associated here with judges as those who stand in the middle."},
    "δίχαιον": {"english": "divided in two", "alternate": None, "category": "greek_term", "transliteration": "dichaion", "note": "Etymological form used in the discussion of justice and division."},
    "δικάστης": {"english": "judge", "alternate": None, "category": "greek_term", "transliteration": "dikastēs", "note": "Greek term for judge, discussed etymologically in connection with division and justice."},
    "διχάστης": {"english": "divider in two", "alternate": None, "category": "greek_term", "transliteration": "dichastēs", "note": "Etymological form used to explain the judge as one who divides."},
    "χάριτες": {"english": "Graces", "alternate": "acts of favor", "category": "greek_term", "transliteration": "charites", "note": "The plural form associated with reciprocal favor and requital."},
    "νομισμα": {"english": "currency", "alternate": "money", "category": "greek_term", "transliteration": "nomisma", "note": "Unaccented form of the Greek word for money or currency; source accenting should be checked."},
    "νόμος": {"english": "law", "alternate": "custom", "category": "greek_term", "transliteration": "nomos", "note": "Greek term for law, custom, or convention."},
    "νόμισμα": {"english": "currency", "alternate": "money", "category": "greek_term", "transliteration": "nomisma", "note": "Greek term for money or currency, connected with convention or law."},
    "δικαιοπράγημα": {"english": "just act", "alternate": None, "category": "greek_term", "transliteration": "dikaiopragēma", "note": "A term for a just action or performance of justice."},
    "δικαίωμα": {"english": "corrective just act", "alternate": "legal claim", "category": "greek_term", "transliteration": "dikaiōma", "note": "A stricter term connected with corrective justice or a legally just act."},
    "ἀκρατής": {"english": "person lacking self-control", "alternate": "incontinent person", "category": "greek_term", "transliteration": "akratēs", "note": "Greek term for a person who lacks self-control."},
    "συνιέναι": {"english": "to understand", "alternate": None, "category": "greek_term", "transliteration": "synienai", "note": "Greek infinitive meaning to understand or comprehend."},
    "εὖ": {"english": "well", "alternate": None, "category": "greek_term", "transliteration": "eu", "note": "Greek adverb meaning well."},
    "καλῶς": {"english": "well", "alternate": "nobly", "category": "greek_term", "transliteration": "kalōs", "note": "Greek adverb meaning well, finely, or nobly."},
    "μανθάνειν": {"english": "to learn", "alternate": None, "category": "greek_term", "transliteration": "manthanein", "note": "Greek infinitive meaning to learn."},
    "εὐγνώμονες": {"english": "fair-minded people", "alternate": "people of good judgment", "category": "greek_term", "transliteration": "eugnōmones", "note": "Greek term for people with good judgment or fair-mindedness."},
    "συγγνώμη": {"english": "allowance", "alternate": "forgiveness", "category": "greek_term", "transliteration": "syngnōmē", "note": "Greek term for allowance, pardon, or sympathetic judgment."},
    "ἐπισπήμη": {"english": "scientific knowledge", "alternate": None, "category": "greek_term", "transliteration": "epistēmē", "note": "Likely source/OCR form for ἐπιστήμη, scientific knowledge; verify against source."},
    "θέσις": {"english": "thesis", "alternate": "position", "category": "greek_term", "transliteration": "thesis", "note": "Greek term for a thesis, position, or something set down."},
    "σεῖος ἀνὴρ": {"english": "godlike man", "alternate": "divine man", "category": "greek_phrase", "transliteration": "theios anēr", "note": "Likely source/OCR form for θεῖος ἀνήρ, a godlike or divine man; verify against source."},
    "φιλοπάτωρ": {"english": "father-loving", "alternate": "lover of one's father", "category": "greek_term", "transliteration": "philopatōr", "note": "Greek epithet meaning father-loving."},
    "μακάριος": {"english": "blessed", "alternate": "happy", "category": "greek_term", "transliteration": "makarios", "note": "Greek adjective meaning blessed or happy."},
    "χαίρειν": {"english": "to rejoice", "alternate": "to take pleasure", "category": "greek_term", "transliteration": "chairein", "note": "Greek infinitive meaning to rejoice or take pleasure."},
    "κινήσεις": {"english": "movements", "alternate": "motions", "category": "greek_term", "transliteration": "kinēseis", "note": "Greek plural meaning movements or motions."},
    "γενέσεις": {"english": "comings-to-be", "alternate": "processes of becoming", "category": "greek_term", "transliteration": "geneseis", "note": "Greek plural meaning generations, origins, or processes of coming-to-be."},
}

PROPER_NAME_MAP: dict[str, dict[str, str]] = {
    "Plato": {"category": "philosopher", "note": "Greek philosopher and teacher of Aristotle; referenced in connection with first principles and philosophical inquiry."},
    "Eudoxus": {"category": "philosopher", "note": "Greek philosopher and mathematician associated with a hedonist theory discussed by Aristotle."},
    "Speusippus": {"category": "philosopher", "note": "Plato's nephew and successor as head of the Academy."},
    "Pythagoreans": {"category": "philosophical_school", "note": "Followers of Pythagorean philosophy, often associated with numerical and moral dualisms."},
    "Sardanapalus": {"category": "king_or_ruler", "note": "Legendary Assyrian king associated in Greek literature with luxury and sensual indulgence."},
    "Hesiod": {"category": "poet_or_author", "note": "Early Greek poet cited as moral and practical authority."},
    "Solon": {"category": "lawgiver", "note": "Athenian lawgiver and poet, often cited as a model of Greek political wisdom."},
    "Priam": {"category": "mythological_or_literary_reference", "note": "Legendary king of Troy in Homeric tradition."},
    "Heraclitus": {"category": "philosopher", "note": "Pre-Socratic Greek philosopher associated with change, logos, and paradoxical sayings."},
    "Theognis": {"category": "poet_or_author", "note": "Greek elegiac poet often cited for aristocratic moral reflection."},
    "Pericles": {"category": "historical_person", "note": "Athenian statesman and general of the classical period."},
    "Pittacus": {"category": "lawgiver", "note": "One of the Seven Sages of Greece and ruler of Mytilene."},
    "Lacedaemonians": {"category": "people_or_region", "note": "The Spartans; inhabitants of Lacedaemon."},
    "Cretans": {"category": "people_or_region", "note": "People of Crete, frequently cited in Greek political comparison."},
    "Milo": {"category": "historical_person", "note": "Milo of Croton, famed Greek athlete used as an example of bodily strength."},
    "Epicharmus": {"category": "poet_or_author", "note": "Greek comic poet associated with Sicilian comedy."},
    "Euripus": {"category": "city", "note": "The Euripus strait, proverbial for rapid and unstable currents."},
    "Phoenissae": {"category": "mythological_or_literary_reference", "note": "The Phoenician Women, a tragedy traditionally associated with Euripides."},
}

NAME_PREFIX_BY_CATEGORY = {
    "city": "P",
    "people_or_region": "P",
    "technical_concept": "C",
}

ROMAN_TO_INT = {
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
}


@dataclass(frozen=True)
class Replacement:
    source: str
    target: str


def _roman_or_int(value: str) -> int | None:
    token = value.strip().upper()
    if token.isdigit():
        return int(token)
    return ROMAN_TO_INT.get(token)


def _heading_key(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    book = BOOK_RE.match(stripped)
    if book:
        token = book.group(1).upper()
        number = _roman_or_int(token)
        canonical = EXPECTED_BOOK_ORDER[number - 1] if number and 1 <= number <= len(EXPECTED_BOOK_ORDER) else token
        return ("book", canonical)
    chapter = CHAPTER_RE.match(stripped)
    if chapter:
        number = _roman_or_int(chapter.group(1))
        return ("chapter", str(number or chapter.group(1).upper()))
    return None


def remove_duplicate_adjacent_headings(text: str) -> tuple[str, int]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    output: list[str] = []
    last_heading: tuple[str, str] | None = None
    only_blank_since_heading = False
    removed = 0

    for line in lines:
        key = _heading_key(line)
        if key is not None:
            if key == last_heading and only_blank_since_heading:
                removed += 1
                continue
            output.append(line)
            last_heading = key
            only_blank_since_heading = True
            continue

        output.append(line)
        if line.strip():
            only_blank_since_heading = False

    return "\n".join(output).rstrip() + "\n", removed


def detected_book_order(text: str) -> list[str]:
    books: list[str] = []
    for line in text.splitlines():
        match = BOOK_RE.match(line)
        if not match:
            continue
        token = match.group(1).upper()
        number = _roman_or_int(token)
        if number and 1 <= number <= len(EXPECTED_BOOK_ORDER):
            token = EXPECTED_BOOK_ORDER[number - 1]
        books.append(token)
    return books


def validate_book_order(text: str) -> dict[str, Any]:
    books = detected_book_order(text)
    return {
        "expected": EXPECTED_BOOK_ORDER,
        "detected": books,
        "valid": books == EXPECTED_BOOK_ORDER,
    }


def validate_chapters_nondecreasing_by_book(text: str) -> dict[str, Any]:
    current_book: str | None = None
    last_chapter_by_book: dict[str, int] = {}
    errors: list[dict[str, Any]] = []

    for line_no, line in enumerate(text.splitlines(), 1):
        book = BOOK_RE.match(line)
        if book:
            token = book.group(1).upper()
            number = _roman_or_int(token)
            current_book = EXPECTED_BOOK_ORDER[number - 1] if number and 1 <= number <= 10 else token
            last_chapter_by_book.setdefault(current_book, 0)
            continue

        chapter = CHAPTER_RE.match(line)
        if not chapter:
            continue
        chapter_number = _roman_or_int(chapter.group(1))
        if chapter_number is None:
            continue
        book_key = current_book or "__front__"
        previous = last_chapter_by_book.get(book_key, 0)
        if chapter_number < previous:
            errors.append(
                {
                    "line": line_no,
                    "book": current_book,
                    "previous_chapter": previous,
                    "chapter": chapter_number,
                }
            )
        last_chapter_by_book[book_key] = max(previous, chapter_number)

    return {
        "valid": not errors,
        "errors": errors,
        "last_chapter_by_book": last_chapter_by_book,
    }


def load_replacements(path: Path = DEFAULT_REPLACEMENTS_CONFIG) -> list[Replacement]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        Replacement(source=str(item["from"]), target=str(item["to"]))
        for item in payload.get("replacements", [])
    ]


def apply_replacements(text: str, replacements: list[Replacement]) -> tuple[str, list[dict[str, Any]]]:
    report: list[dict[str, Any]] = []
    output = text
    for replacement in sorted(replacements, key=lambda item: len(item.source), reverse=True):
        count = output.count(replacement.source)
        if count:
            output = output.replace(replacement.source, replacement.target)
        report.append(
            {
                "from": replacement.source,
                "to": replacement.target,
                "count": count,
            }
        )
    return output, report


def normalize_greek_term(term: str) -> str:
    return unicodedata.normalize("NFC", re.sub(r"[ \t]+", " ", term.strip())).lower()


def _line_number_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _context_snippet(text: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _current_location(text: str, offset: int) -> dict[str, str | None]:
    current_book: str | None = None
    current_chapter: str | None = None
    cursor = 0
    for line in text.splitlines(keepends=True):
        if cursor > offset:
            break
        stripped = line.strip()
        book = BOOK_RE.match(stripped)
        if book:
            token = book.group(1).upper()
            number = _roman_or_int(token)
            current_book = f"BOOK {EXPECTED_BOOK_ORDER[number - 1]}" if number and 1 <= number <= 10 else f"BOOK {token}"
            current_chapter = None
        chapter = CHAPTER_RE.match(stripped)
        if chapter:
            number = _roman_or_int(chapter.group(1))
            current_chapter = f"Chapter {number}" if number is not None else stripped
        cursor += len(line)
    return {"book": current_book, "chapter": current_chapter}


def _glossary_id(prefix: str, index: int) -> str:
    return f"{prefix}{index:02d}"


def _english_for_unknown_greek(term: str) -> str:
    return "Greek term"


def detect_greek_glossary_candidates(text: str) -> list[dict[str, Any]]:
    found: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for match in GREEK_RE.finditer(text):
        term = match.group(0)
        normalized = normalize_greek_term(term)
        configured = GREEK_TRANSLATION_MAP.get(normalized)
        item = found.get(normalized)
        if item is None:
            english = str((configured or {}).get("body") or (configured or {}).get("english") or _english_for_unknown_greek(term))
            alternate = (configured or {}).get("alternate")
            found[normalized] = {
                "greek_term": term,
                "normalized_greek_term": normalized,
                "transliteration": (configured or {}).get("transliteration"),
                "suggested_english": english,
                "english": f"{english}; {alternate}" if alternate else english,
                "category": (configured or {}).get("category") or "greek_term",
                "note": (configured or {}).get("note") or "Greek term preserved from the source text; translation requires editorial review.",
                "first_line": _line_number_for_offset(text, match.start()),
                "first_occurrence": _current_location(text, match.start()),
                "occurrence_count": 1,
                "context_snippet": _context_snippet(text, match.start(), match.end()),
                "needs_source_check": configured is None,
            }
        else:
            item["occurrence_count"] += 1
    return list(found.values())


def _consolidate_split_greek_phrases(text: str) -> tuple[str, int]:
    replacements = {
        "ἐν βίῳ\nτελείῳ": "ἐν βίῳ τελείῳ",
    }
    count = 0
    output = text
    for source, target in replacements.items():
        occurrences = output.count(source)
        if occurrences:
            output = output.replace(source, target)
            count += occurrences
    return output, count


def replace_greek_with_english_markers(text: str) -> tuple[str, list[dict[str, Any]], int]:
    text, _split_phrase_count = _consolidate_split_greek_phrases(text)
    candidates = detect_greek_glossary_candidates(text)
    normalized_to_id = {
        str(item["normalized_greek_term"]): _glossary_id("G", index)
        for index, item in enumerate(candidates, 1)
    }
    marked_once: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        term = match.group(0)
        normalized = normalize_greek_term(term)
        item = next((candidate for candidate in candidates if candidate["normalized_greek_term"] == normalized), None)
        english = str((item or {}).get("suggested_english") or _english_for_unknown_greek(term))
        glossary_id = normalized_to_id[normalized]
        if normalized not in marked_once:
            marked_once.add(normalized)
            return f"{english} [{glossary_id}]"
        return english

    output = GREEK_RE.sub(_replace, text)
    entries: list[dict[str, Any]] = []
    for item in candidates:
        glossary_id = normalized_to_id[str(item["normalized_greek_term"])]
        entries.append(
            {
                "id": glossary_id,
                "category": item["category"],
                "display": item["suggested_english"],
                "original": item["greek_term"],
                "greek": item["greek_term"],
                "transliteration": item["transliteration"],
                "english": item["english"],
                "first_occurrence": item["first_occurrence"],
                "occurrence_count": item["occurrence_count"],
                "note": item["note"],
                "needs_source_check": item["needs_source_check"],
                "context_snippet": item["context_snippet"],
            }
        )
    return output, entries, len(marked_once)


def mark_proper_names(text: str, existing_entries: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], int]:
    output = text
    entries: list[dict[str, Any]] = []
    counters = {"N": 0, "P": 0, "C": 0}
    marked = 0
    for name, config in PROPER_NAME_MAP.items():
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        match = pattern.search(output)
        if not match:
            continue
        category = config["category"]
        prefix = NAME_PREFIX_BY_CATEGORY.get(category, "N")
        counters[prefix] += 1
        entry_id = _glossary_id(prefix, counters[prefix])
        replacement = f"{name} [{entry_id}]"
        output = output[: match.start()] + replacement + output[match.end() :]
        entries.append(
            {
                "id": entry_id,
                "category": category,
                "display": name,
                "name": name,
                "original": name,
                "english": name,
                "first_occurrence": _current_location(output, match.start()),
                "occurrence_count": len(pattern.findall(text)),
                "note": config["note"],
            }
        )
        marked += 1
    return output, existing_entries + entries, marked


def build_glossary_json(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "book_id": "book_0029",
        "title": "Nicomachean Ethics",
        "language": "en-us",
        "entries": entries,
    }


def build_glossary_markdown(entries: list[dict[str, Any]]) -> str:
    groups = [
        ("Greek Terms", {"greek_term", "greek_phrase"}),
        ("Philosophers and Schools", {"philosopher", "philosophical_school"}),
        (
            "Kings, Cities, Peoples, and Historical References",
            {
                "king_or_ruler",
                "lawgiver",
                "poet_or_author",
                "city",
                "people_or_region",
                "mythological_or_literary_reference",
                "historical_person",
            },
        ),
        ("Technical Concepts", {"technical_concept"}),
    ]
    lines = ["# Glossary"]
    for title, categories in groups:
        section_entries = [entry for entry in entries if entry.get("category") in categories]
        if not section_entries:
            continue
        lines.extend(["", f"## {title}"])
        for entry in section_entries:
            display = str(entry.get("display") or entry.get("english") or entry.get("name") or entry["id"])
            lines.extend(["", f"### [{entry['id']}] {display.title() if entry['id'].startswith('G') else display}"])
            if entry.get("greek"):
                lines.append(f"**Greek:** {entry['greek']}  ")
            if entry.get("transliteration"):
                lines.append(f"**Transliteration:** {entry['transliteration']}  ")
            if entry.get("english"):
                lines.append(f"**Meaning:** {entry['english']}.  ")
            if entry.get("note"):
                lines.append(f"**Note:** {entry['note']}")
    return "\n".join(lines).rstrip() + "\n"


def validate_glossary_references(text: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    entry_ids = {str(entry["id"]) for entry in entries}
    marker_ids = set(GLOSSARY_MARKER_RE.findall(text))
    return {
        "valid": marker_ids == entry_ids,
        "markers_without_entries": sorted(marker_ids - entry_ids),
        "entries_without_markers": sorted(entry_ids - marker_ids),
        "duplicate_entry_ids": sorted(
            entry_id for entry_id in entry_ids if [entry["id"] for entry in entries].count(entry_id) > 1
        ),
    }


def run_cleanup(
    input_path: Path,
    *,
    clean_path: Path,
    report_path: Path,
    glossary_path: Path,
    glossary_md_path: Path | None = None,
    replacements_config: Path = DEFAULT_REPLACEMENTS_CONFIG,
    mark_repeated_greek: bool = False,
) -> dict[str, Any]:
    original = input_path.read_text(encoding="utf-8")
    text, duplicate_headings_removed = remove_duplicate_adjacent_headings(original)
    replacements = load_replacements(replacements_config)
    text, replacement_report = apply_replacements(text, replacements)
    text, glossary_entries, greek_markers_inserted = replace_greek_with_english_markers(text)
    text, glossary_entries, proper_names_marked = mark_proper_names(text, glossary_entries)

    book_validation = validate_book_order(text)
    chapter_validation = validate_chapters_nondecreasing_by_book(text)
    glossary_validation = validate_glossary_references(text, glossary_entries)
    remaining_greek = detect_greek_glossary_candidates(text)
    remaining_self_mastery_terms = _remaining_self_mastery_terms(text)
    warnings = _self_control_review_warnings(text)
    needs_source_check = [
        {
            "observed": entry.get("original"),
            "likely": None,
            "english": entry.get("english"),
            "status": "needs_source_check",
        }
        for entry in glossary_entries
        if entry.get("needs_source_check")
    ]
    validation_failures = []
    gloss_placeholders_remaining = len(re.findall(r"Greek (?:term|phrase|word) \[G", text))
    old_gloss_markers_remaining = text.count("{{GLOSS:")
    if "{{GLOSS:" in text:
        validation_failures.append("raw_gloss_markers_remaining")
    if gloss_placeholders_remaining:
        validation_failures.append("gloss_placeholders_remaining")
    if remaining_greek:
        validation_failures.append("untranslated_greek_remaining")
    if remaining_self_mastery_terms:
        validation_failures.append("self_mastery_terms_remaining")
    if not glossary_validation["valid"]:
        validation_failures.append("glossary_reference_mismatch")
    if not book_validation["valid"]:
        validation_failures.append("book_order_invalid")
    if not chapter_validation["valid"]:
        validation_failures.append("chapter_order_invalid")
    if _note_markers(original) != _note_markers(text):
        validation_failures.append("note_markers_changed")
    status = "PASSED" if not validation_failures else "FAILED"
    glossary_md_path = glossary_md_path or glossary_path.with_suffix(".md")
    glossary_json = build_glossary_json(glossary_entries)
    glossary_md = build_glossary_markdown(glossary_entries)

    report = {
        "stage": STAGE_NAME,
        "status": status,
        "input_path": str(input_path),
        "clean_path": str(clean_path),
        "report_path": str(report_path),
        "glossary_json_path": str(glossary_path),
        "glossary_md_path": str(glossary_md_path),
        "duplicate_adjacent_headings_removed": duplicate_headings_removed,
        "raw_greek_remaining": len(remaining_greek),
        "gloss_placeholders_remaining": gloss_placeholders_remaining,
        "old_gloss_markers_remaining": old_gloss_markers_remaining,
        "self_mastery_residue_remaining": len(remaining_self_mastery_terms),
        "self_control_occurrences_reviewed": len(warnings),
        "glossary_markers_found": len(GLOSSARY_MARKER_RE.findall(text)),
        "glossary_entries_found": len(glossary_entries),
        "missing_glossary_entries": glossary_validation["markers_without_entries"],
        "orphan_glossary_entries": glossary_validation["entries_without_markers"],
        "places_and_peoples_marked": sum(1 for entry in glossary_entries if str(entry["id"]).startswith("P")),
        "gods_and_divine_figures_marked": sum(1 for entry in glossary_entries if str(entry["id"]).startswith("D")),
        "greek_terms_found": greek_markers_inserted,
        "greek_terms_translated": greek_markers_inserted,
        "proper_names_marked": proper_names_marked,
        "self_mastery_replacements": sum(item["count"] for item in replacement_report),
        "remaining_self_mastery_terms": remaining_self_mastery_terms,
        "warnings": warnings,
        "needs_source_check": needs_source_check,
        "greek_terms_detected": greek_markers_inserted,
        "greek_markers_inserted": greek_markers_inserted,
        "book_order": book_validation,
        "chapters_nondecreasing_by_book": chapter_validation,
        "glossary_references": glossary_validation,
        "validation_failures": validation_failures,
        "terminology_replacements": replacement_report,
        "note_markers_preserved": _note_markers(original) == _note_markers(text),
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    glossary_path.parent.mkdir(parents=True, exist_ok=True)
    glossary_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    glossary_path.write_text(json.dumps(glossary_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    glossary_md_path.write_text(glossary_md, encoding="utf-8")

    if status != "PASSED":
        return report

    clean_path.parent.mkdir(parents=True, exist_ok=True)
    clean_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return report


def _note_markers(text: str) -> list[str]:
    return re.findall(r"\[\d+\]", text)


def _remaining_self_mastery_terms(text: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"Self-Mastery|self-mastery|self-mastering|perfect self-mastery|perfected self-mastery",
        re.IGNORECASE,
    )
    found: OrderedDict[str, dict[str, str]] = OrderedDict()
    for match in pattern.finditer(text):
        found.setdefault(
            match.group(0),
            {"term": match.group(0), "status": "replace", "replacement": "temperance"},
        )
    return list(found.values())


def _self_control_review_warnings(text: str) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for term in ("self-control", "self-controlled", "lack of self-control"):
        for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            warnings.append(
                {
                    "term": term,
                    "line": str(_line_number_for_offset(text, match.start())),
                    "status": "allowed",
                    "reason": "General psychological restraint, not the named virtue.",
                }
            )
    return warnings


def default_output_paths(input_path: Path) -> tuple[Path, Path, Path, Path]:
    directory = input_path.parent
    base = "book_0029_nicomachean_ethics"
    return (
        directory / f"{base}_en_us_FINAL_CLEAN.txt",
        directory / f"{base}_final_cleanup_report.json",
        directory / f"{base}_glossary_FINAL.json",
        directory / f"{base}_glossary_FINAL.md",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic EN-US post-refine cleanup.")
    parser.add_argument("input")
    parser.add_argument("--clean-output", default=None)
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--glossary-output", default=None)
    parser.add_argument("--glossary-md-output", default=None)
    parser.add_argument("--replacements-config", default=str(DEFAULT_REPLACEMENTS_CONFIG))
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    clean_default, report_default, glossary_default, glossary_md_default = default_output_paths(input_path)
    report = run_cleanup(
        input_path,
        clean_path=Path(args.clean_output) if args.clean_output else clean_default,
        report_path=Path(args.report_output) if args.report_output else report_default,
        glossary_path=Path(args.glossary_output) if args.glossary_output else glossary_default,
        glossary_md_path=Path(args.glossary_md_output) if args.glossary_md_output else glossary_md_default,
        replacements_config=Path(args.replacements_config),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
