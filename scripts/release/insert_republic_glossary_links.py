#!/usr/bin/env python3
"""Insert glossary links into the Republic EPUB.

Writes a new EPUB and a JSON validation report. The source text is not rewritten:
only controlled superscript glossary links are inserted into Book 1 through Book 10.
"""

from __future__ import annotations

import json
import posixpath
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from bs4 import BeautifulSoup, NavigableString


ROOT = Path(__file__).resolve().parents[2]
REQUESTED_INPUT = ROOT / "data/builds/book_0027/en/republic_of_plato_BOOK_FINAL.epub"
FALLBACK_INPUT = ROOT / "data/builds/book_0027/en/republic_of_plato_BOOK_FIXED.epub"
OUTPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_BOOK_GLOSSARY.epub"
REPORT_PATH = ROOT / "data/builds/book_0027/en/republic_glossary_links_report.json"

NS_XHTML = "http://www.w3.org/1999/xhtml"
GLOSSARY_LABEL = "Glossary of Key Terms and Names"
GLOSSARY_PATH = "EPUB/text/glossary.xhtml"
GLOSSARY_HREF_FROM_TEXT = "glossary.xhtml"


@dataclass(frozen=True)
class Term:
    number: int
    label: str
    slug: str
    definition: str
    pattern: str
    min_book: int = 1
    max_book: int = 10
    validator: str = ""

    @property
    def glossary_id(self) -> str:
        return f"glossary-{self.number:02d}-{self.slug}"

    @property
    def ref_id(self) -> str:
        return f"ref-{self.number:02d}-{self.slug}"


TERMS: list[Term] = [
    Term(1, "Adeimantus", "adeimantus", "Brother of Glaucon and one of Socrates’ main interlocutors in The Republic. Along with Glaucon, he presses Socrates to defend justice not merely for its rewards, but for its value in itself.", r"\bAdeimantus\b"),
    Term(2, "Appetite", "appetite", "The desiring part of the soul. It seeks bodily pleasures, money, food, drink, sex, comfort, and possession. In Plato’s psychology, appetite must be governed by reason if the soul is to be just.", r"\bappetite\b|\bappetitive\b", 4, 4),
    Term(3, "Aristocracy", "aristocracy", "In Plato’s highest sense, rule by the best. In The Republic, the best rulers are those whose souls are governed by reason and who understand the Good.", r"\baristocracy\b", 8, 8),
    Term(4, "Auxiliaries", "auxiliaries", "The warrior-defender class in the ideal city. They support the rulers, protect the city, and must be trained in courage, discipline, and loyalty to reason.", r"\bauxiliaries\b|\bauxiliary\b", 2),
    Term(5, "Cephalus", "cephalus", "An elderly and wealthy man at whose house the discussion begins. He represents a traditional, respectable view of justice as truthfulness, repayment of debts, and religious duty.", r"\bCephalus\b"),
    Term(6, "City in Speech", "city-in-speech", "The ideal city constructed through argument in The Republic. It is not merely a political proposal, but a way of magnifying justice so that it can be examined more clearly in the individual soul.", r"\bCity in Speech\b", 1),
    Term(7, "Craft", "craft", "A skill or disciplined practice directed toward a proper object. Medicine serves the body; navigation serves sailors; ruling, properly understood, should serve the ruled. The Greek idea behind this term is often connected with technē.", r"\bcrafts?\b", validator="craft_philosophical"),
    Term(8, "Democracy", "democracy", "A regime marked by freedom, equality, and variety of desires. Plato admires its energy but fears its instability when freedom becomes detached from discipline and truth.", r"\bdemocracy\b|\bdemocratic\b", 8, 8),
    Term(9, "Dialectic", "dialectic", "The highest form of philosophical inquiry. It moves beyond opinion and hypothesis toward knowledge of first principles, especially the Good.", r"\bdialectic\b", 6),
    Term(10, "Education", "education", "For Plato, education is not merely the transfer of information. It is the formation of character, desire, courage, judgment, and reason. The education of the guardians is central to the justice of the city.", r"\beducation\b", 2),
    Term(11, "Form", "form", "An eternal and intelligible reality beyond changing visible things. Beautiful things participate in Beauty; just acts participate in Justice. The Forms represent what is truly knowable, as opposed to mere appearances.", r"\bForms?\b|\bForm\b", 5, validator="form_philosophical"),
    Term(12, "Glaucon", "glaucon", "Brother of Adeimantus and one of the most important speakers in The Republic. He challenges Socrates to prove that justice is good in itself, even when stripped of reputation and rewards.", r"\bGlaucon\b"),
    Term(13, "The Good", "the-good", "The highest object of knowledge in Plato’s philosophy. The Good is compared to the sun: as the sun makes visible things visible, the Good makes truth and knowledge possible.", r"\bIdea of the Good\b|\bForm of the Good\b|\bthe Good\b|\bGood\b", 6, validator="the_good_philosophical"),
    Term(14, "Guardians", "guardians", "The class responsible for protecting and governing the ideal city. The best guardians become rulers; the others serve as auxiliaries. Their education is one of the central concerns of The Republic.", r"\bguardians\b|\bguardian\b", 2),
    Term(15, "Gyges", "gyges", "A legendary Lydian shepherd associated with the famous story of the ring that grants invisibility. In The Republic, the Ring of Gyges tests whether a person would remain just if he could act without being seen or punished.", r"\bGyges\b", 2),
    Term(16, "Injustice", "injustice", "Disorder in the soul and the city. Injustice occurs when the lower parts rule the higher: appetite over reason, desire over wisdom, faction over harmony.", r"\binjustice\b", validator="not_heading_like"),
    Term(17, "Justice", "justice", "The central subject of The Republic. Plato gradually moves beyond conventional definitions and presents justice as proper order: in the city, each class performs its proper function; in the soul, reason rules, spirit supports reason, and appetite obeys.", r"\bjustice\b", validator="justice_substantial"),
    Term(18, "Kallipolis", "kallipolis", "A later term often used to describe the ideal city discussed in The Republic. It means the “beautiful city” or “noble city.”", r"\bKallipolis\b"),
    Term(19, "Moderation", "moderation", "Harmony between the parts of the soul or city concerning who should rule. In the just soul, appetite accepts the rule of reason; in the just city, lower classes accept the guidance of the wise.", r"\bmoderation\b|\btemperance\b", 4, 4, "moderation_philosophical"),
    Term(20, "Oligarchy", "oligarchy", "Rule by the wealthy. Plato presents oligarchy as a regime in which money becomes the chief measure of honor and political authority.", r"\boligarchy\b|\boligarchic(?:al)?\b", 8, 8),
    Term(21, "Philosopher-Ruler", "philosopher-ruler", "The ruler who loves wisdom and understands the Good. Plato argues that cities will not be healed until philosophers rule, or rulers become truly philosophical.", r"\bphilosopher[- ]rulers?\b|\bphilosophers rule\b|\brulers become truly philosophical\b", 5),
    Term(22, "Piraeus", "piraeus", "The port of Athens, where The Republic begins. Socrates’ descent to the Piraeus is symbolically important: the dialogue begins away from the city center, in a place of movement, commerce, festival, and social mixture.", r"\bPiraeus\b"),
    Term(23, "Plato", "plato", "The Athenian philosopher who wrote The Republic. A student of Socrates and teacher of Aristotle, Plato used dialogue to investigate justice, knowledge, education, politics, the soul, and the Good.", r"\bPlato\b"),
    Term(24, "Polemarchus", "polemarchus", "Son of Cephalus. He inherits the argument from his father and defines justice as helping friends and harming enemies. Socrates challenges and dismantles this view.", r"\bPolemarchus\b"),
    Term(25, "Reason", "reason", "The highest part of the soul. Reason seeks truth and should govern both spirit and appetite. A just soul is one in which reason rules.", r"\breason\b|\brational\b", 4, 4, "reason_tripartite"),
    Term(26, "Ring of Gyges", "ring-of-gyges", "A thought experiment used by Glaucon. If a person could become invisible and act without consequences, would he remain just? The story challenges Socrates to prove that justice is valuable in itself.", r"\bring of Gyges\b|\bgold ring\b|\bring\b", 2, 2, "ring_gyges_story"),
    Term(27, "Socrates", "socrates", "The central speaker and narrator of The Republic. Socrates questions others, tests definitions, exposes contradictions, and leads the inquiry into justice, the soul, education, and the ideal city.", r"\bSocrates\b"),
    Term(28, "Sophist", "sophist", "A professional teacher or rhetorician in ancient Greece, often associated with persuasion, public success, and argument. Thrasymachus represents a sophistic challenge to traditional morality.", r"\bsophist\b|\bsophists\b"),
    Term(29, "Soul", "soul", "The inner structure of the human being. In The Republic, the soul has three parts: reason, spirit, and appetite. Justice is the proper ordering of these parts.", r"\bsouls?\b", validator="soul_philosophical"),
    Term(30, "Spirit", "spirit", "The spirited part of the soul, associated with courage, anger, honor, ambition, indignation, and the desire for recognition. In the just soul, spirit supports reason against unruly appetite.", r"\bspirit\b|\bspirited\b", 4, 4, "spirit_tripartite"),
    Term(31, "State", "state", "The political community or city. In this edition, “state” may refer broadly to the organized political order discussed by Plato.", r"\bstates?\b"),
    Term(32, "Thrasymachus", "thrasymachus", "A forceful speaker who claims that justice is the advantage of the stronger. His challenge gives the dialogue its first major confrontation with political realism and the idea that power defines justice.", r"\bThrasymachus\b"),
    Term(33, "Timocracy", "timocracy", "A regime ruled by honor, ambition, and military spirit. In Plato’s account of political decline, timocracy comes after aristocracy and before oligarchy.", r"\btimocracy\b|\btimocratic\b|\btimarchy\b", 8, 8),
    Term(34, "Tyranny", "tyranny", "The worst regime in Plato’s political psychology. It is ruled by lawless appetite and fear. The tyrant appears powerful but is inwardly enslaved.", r"\btyranny\b|\btyrant\b|\btyrannical\b", 8),
    Term(35, "Virtue", "virtue", "Excellence of character or function. In the soul, virtue means proper order and fulfillment of one’s highest nature. Justice is one of the central virtues.", r"\bvirtue\b|\bvirtues\b"),
    Term(36, "Wisdom", "wisdom", "The knowledge required to rule well. In the city, wisdom belongs especially to the rulers; in the soul, it belongs to reason.", r"\bwisdom\b", validator="wisdom_philosophical"),
]


EXCLUDED_ANCESTORS = {
    "a",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "img",
    "metadata",
    "nav",
    "script",
    "style",
    "sup",
    "title",
}


def validator_ok(term: Term, node_text: str, match: re.Match[str], full_paragraph: str) -> bool:
    text = full_paragraph
    lower = text.lower()
    matched = match.group(0).lower()

    if term.validator == "craft_philosophical":
        return "craft of making money" not in lower
    if term.validator == "form_philosophical":
        return (
            "form from the things that partake" in lower
            or "form of beauty" in lower
            or "forms themselves" in lower
            or "form of the good" in lower
            or "corresponding idea or form" in lower
        )
    if term.validator == "justice_substantial":
        if "cherishes the soul" in lower:
            return False
        return True
    if term.validator == "moderation_philosophical":
        return "temperance" in matched or "moderation" in matched
    if term.validator == "not_heading_like":
        return True
    if term.validator == "reason_tripartite":
        return "derived from reason" in lower or "man reasons" in lower or "side of his reason" in lower
    if term.validator == "ring_gyges_story":
        return "gyges" in lower or "gold ring" in lower
    if term.validator == "soul_philosophical":
        return "soul a function" in lower or "functions proper to the soul" in lower or "elements existing in the soul" in lower
    if term.validator == "spirit_tripartite":
        return "passion, or spirit" in lower or "his spirit is on the side of his reason" in lower or "spirit appeared" in lower
    if term.validator == "the_good_philosophical":
        return "idea of the good" in lower or "form of the good" in lower or "the good begot" in lower
    if term.validator == "wisdom_philosophical":
        return "wisdom of socrates" not in lower
    return True


def source_epub() -> tuple[Path, bool]:
    if REQUESTED_INPUT.exists():
        return REQUESTED_INPUT, False
    if FALLBACK_INPUT.exists():
        return FALLBACK_INPUT, True
    raise FileNotFoundError(f"Missing input EPUB: {REQUESTED_INPUT}")


def extract_epub(epub: Path, workdir: Path) -> None:
    with zipfile.ZipFile(epub) as zf:
        zf.extractall(workdir)


def write_epub(source_dir: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w") as zf:
        mimetype = source_dir / "mimetype"
        if mimetype.exists():
            zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(source_dir.rglob("*")):
            if path.is_dir() or path.name == "mimetype":
                continue
            rel = path.relative_to(source_dir).as_posix()
            zf.write(path, rel, compress_type=zipfile.ZIP_DEFLATED)


def excluded_text_node(node: NavigableString) -> bool:
    if not str(node).strip():
        return True
    parent = node.parent
    while parent is not None and getattr(parent, "name", None):
        if parent.name in EXCLUDED_ANCESTORS:
            return True
        parent = parent.parent
    return False


def paragraph_text(node: NavigableString) -> str:
    parent = node.parent
    while parent is not None and getattr(parent, "name", None) != "p":
        parent = parent.parent
    if parent is None:
        return str(node)
    return parent.get_text(" ", strip=True)


def insert_references(workdir: Path) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    linked: dict[str, dict[str, str]] = {}
    events: list[dict[str, str]] = []
    remaining = {term.number: term for term in TERMS}
    compiled = {term.number: re.compile(term.pattern, re.IGNORECASE) for term in TERMS}

    for book_num in range(1, 11):
        rel = f"EPUB/text/book_{book_num:02d}.xhtml"
        path = workdir / rel
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        body = soup.find("body")
        if body is None:
            continue
        for text_node in list(body.find_all(string=True)):
            if excluded_text_node(text_node):
                continue
            node_text = str(text_node)
            para = paragraph_text(text_node)
            matches = []
            for term in list(remaining.values()):
                if not (term.min_book <= book_num <= term.max_book):
                    continue
                for match in compiled[term.number].finditer(node_text):
                    if validator_ok(term, node_text, match, para):
                        matches.append((match.start(), match.end(), term, match.group(0)))
                        break
            if not matches:
                continue

            matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
            non_overlapping = []
            occupied_until = -1
            for start, end, term, matched in matches:
                if start < occupied_until:
                    continue
                non_overlapping.append((start, end, term, matched))
                occupied_until = end
            if not non_overlapping:
                continue

            new_nodes = []
            cursor = 0
            for start, end, term, matched in non_overlapping:
                if term.number not in remaining:
                    continue
                if start > cursor:
                    new_nodes.append(NavigableString(node_text[cursor:start]))
                new_nodes.append(NavigableString(node_text[start:end]))
                sup = soup.new_tag("sup")
                sup["class"] = "glossary-ref"
                anchor = soup.new_tag("a")
                anchor["href"] = f"{GLOSSARY_HREF_FROM_TEXT}#{term.glossary_id}"
                anchor["id"] = term.ref_id
                anchor.string = str(term.number)
                sup.append(anchor)
                new_nodes.append(sup)
                cursor = end
                linked[term.label] = {
                    "number": term.number,
                    "term": term.label,
                    "book_file": f"book_{book_num:02d}.xhtml",
                    "ref_id": term.ref_id,
                    "glossary_id": term.glossary_id,
                    "matched_text": matched,
                }
                events.append(
                    {
                        "term": term.label,
                        "book_file": f"book_{book_num:02d}.xhtml",
                        "ref_id": term.ref_id,
                        "matched_text": matched,
                    }
                )
                del remaining[term.number]
            if cursor < len(node_text):
                new_nodes.append(NavigableString(node_text[cursor:]))
            text_node.replace_with(*new_nodes)
        path.write_text(str(soup), encoding="utf-8")
    return linked, events


def glossary_xhtml(linked: dict[str, dict[str, str]]) -> str:
    soup = BeautifulSoup(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Glossary of Key Terms and Names</title>
  <link rel="stylesheet" type="text/css" href="../styles/stylesheet1.css"/>
</head>
<body epub:type="backmatter">
</body>
</html>
""",
        "xml",
    )
    body = soup.find("body")
    section = soup.new_tag("section")
    section["epub:type"] = "glossary"
    section["id"] = "glossary"
    h1 = soup.new_tag("h1")
    h1.string = GLOSSARY_LABEL
    section.append(h1)
    for term in TERMS:
        entry = soup.new_tag("section")
        entry["id"] = term.glossary_id
        h2 = soup.new_tag("h2")
        h2.string = f"{term.number}. {term.label}"
        p = soup.new_tag("p")
        p.string = term.definition
        entry.append(h2)
        entry.append(p)
        backlink = soup.new_tag("p")
        backlink["class"] = "glossary-backlink"
        info = linked.get(term.label)
        if info:
            a = soup.new_tag("a")
            a["href"] = f"{info['book_file']}#{info['ref_id']}"
            a.string = "Back to text"
            backlink.append(a)
        else:
            backlink.string = "No direct reference inserted in the main text."
        entry.append(backlink)
        section.append(entry)
    body.append(section)
    return str(soup)


def update_css(workdir: Path) -> None:
    path = workdir / "EPUB/styles/stylesheet1.css"
    css = path.read_text(encoding="utf-8")
    additions = []
    if ".glossary-ref" not in css:
        additions.append(
            ".glossary-ref {\n"
            "  font-size: 0.75em;\n"
            "  vertical-align: super;\n"
            "  line-height: 0;\n"
            "  text-decoration: none;\n"
            "}\n"
        )
    if ".glossary-backlink" not in css:
        additions.append(".glossary-backlink {\n  font-size: 0.9em;\n}\n")
    if additions:
        path.write_text(css.rstrip() + "\n\n" + "\n".join(additions), encoding="utf-8")


def update_nav(workdir: Path) -> bool:
    path = workdir / "EPUB/nav.xhtml"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    nav = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", id="toc")
    if nav is None:
        return False
    if soup.find("a", href="text/glossary.xhtml#glossary") is None:
        ol = nav.find("ol")
        li = soup.new_tag("li")
        a = soup.new_tag("a")
        a["href"] = "text/glossary.xhtml#glossary"
        a.string = GLOSSARY_LABEL
        li.append(a)
        ol.append(li)
    path.write_text(str(soup), encoding="utf-8")
    return True


def update_ncx(workdir: Path) -> bool:
    path = workdir / "EPUB/toc.ncx"
    if not path.exists():
        return False
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    nav_map = soup.find("navMap")
    if nav_map is None:
        return False
    if soup.find("content", src="text/glossary.xhtml#glossary") is None:
        play_orders = [
            int(node.get("playOrder", "0"))
            for node in soup.find_all("navPoint")
            if str(node.get("playOrder", "")).isdigit()
        ]
        order = max(play_orders or [0]) + 1
        nav_point = soup.new_tag("navPoint")
        nav_point["id"] = f"navPoint-{order}"
        nav_point["playOrder"] = str(order)
        nav_label = soup.new_tag("navLabel")
        text = soup.new_tag("text")
        text.string = GLOSSARY_LABEL
        nav_label.append(text)
        content = soup.new_tag("content")
        content["src"] = "text/glossary.xhtml#glossary"
        nav_point.append(nav_label)
        nav_point.append(content)
        nav_map.append(nav_point)
    path.write_text(str(soup), encoding="utf-8")
    return True


def update_opf(workdir: Path) -> bool:
    path = workdir / "EPUB/content.opf"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    manifest = soup.find("manifest")
    spine = soup.find("spine")
    if manifest is None or spine is None:
        return False
    if soup.find("item", id="text_glossary_xhtml") is None:
        item = soup.new_tag("item")
        item["id"] = "text_glossary_xhtml"
        item["href"] = "text/glossary.xhtml"
        item["media-type"] = "application/xhtml+xml"
        manifest.append(item)
    if soup.find("itemref", idref="text_glossary_xhtml") is None:
        itemref = soup.new_tag("itemref")
        itemref["idref"] = "text_glossary_xhtml"
        spine.append(itemref)
    path.write_text(str(soup), encoding="utf-8")
    return True


def collect_ids(workdir: Path) -> tuple[dict[str, set[str]], list[str]]:
    ids_by_file: dict[str, set[str]] = {}
    duplicates: list[str] = []
    seen_global: set[tuple[str, str]] = set()
    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        rel = path.relative_to(workdir).as_posix()
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        ids: set[str] = set()
        for node in soup.find_all(attrs={"id": True}):
            node_id = str(node["id"])
            if node_id in ids:
                duplicates.append(f"{rel}#{node_id}")
            ids.add(node_id)
            key = (rel, node_id)
            if key in seen_global:
                duplicates.append(f"{rel}#{node_id}")
            seen_global.add(key)
        ids_by_file[rel] = ids
    return ids_by_file, sorted(set(duplicates))


def resolve_href(source_rel: str, href: str) -> tuple[str, str]:
    target, _, fragment = href.partition("#")
    base_dir = posixpath.dirname(source_rel)
    if target:
        target_rel = posixpath.normpath(posixpath.join(base_dir, target))
    else:
        target_rel = source_rel
    return target_rel, fragment


def validate_links(workdir: Path) -> dict[str, object]:
    ids_by_file, duplicate_ids = collect_ids(workdir)
    existing = {path.relative_to(workdir).as_posix() for path in workdir.rglob("*") if path.is_file()}
    broken_links: list[str] = []
    body_refs: dict[str, str] = {}
    glossary_backlinks: dict[str, str] = {}

    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        source_rel = path.relative_to(workdir).as_posix()
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"])
            if re.match(r"^[a-z]+:", href):
                continue
            target_rel, fragment = resolve_href(source_rel, href)
            if target_rel not in existing:
                broken_links.append(f"{source_rel}: missing target {href}")
                continue
            if fragment and fragment not in ids_by_file.get(target_rel, set()):
                broken_links.append(f"{source_rel}: missing fragment {href}")
            anchor_id = str(anchor.get("id", ""))
            if anchor_id.startswith("ref-"):
                body_refs[anchor_id] = source_rel
            if source_rel == GLOSSARY_PATH and href.startswith("book_"):
                glossary_backlinks[href] = source_rel

    return {
        "duplicate_ids": duplicate_ids,
        "broken_links": sorted(set(broken_links)),
        "body_ref_ids": sorted(body_refs),
        "glossary_backlinks": sorted(glossary_backlinks),
    }


def scan_counts(workdir: Path) -> dict[str, int]:
    href_count = 0
    glossary_id_count = 0
    ref_id_count = 0
    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        text = path.read_text(encoding="utf-8")
        href_count += text.count('href="glossary.xhtml#')
        glossary_id_count += len(re.findall(r'id="glossary-', text))
        ref_id_count += len(re.findall(r'id="ref-', text))
    return {
        'href="glossary.xhtml#': href_count,
        'id="glossary-': glossary_id_count,
        'id="ref-': ref_id_count,
    }


def main() -> None:
    input_epub, used_fallback = source_epub()
    with tempfile.TemporaryDirectory(prefix="republic_glossary_") as tmp:
        workdir = Path(tmp)
        extract_epub(input_epub, workdir)

        linked, events = insert_references(workdir)
        (workdir / GLOSSARY_PATH).write_text(glossary_xhtml(linked), encoding="utf-8")
        update_css(workdir)
        nav_updated = update_nav(workdir)
        ncx_updated = update_ncx(workdir)
        opf_updated = update_opf(workdir)
        validation = validate_links(workdir)
        counts = scan_counts(workdir)

        terms_linked = [term.label for term in TERMS if term.label in linked]
        terms_not_found = [term.label for term in TERMS if term.label not in linked]
        backlinks_created = len(validation["glossary_backlinks"])
        final_status = "READY_FOR_KINDLE_PREVIEWER"
        if validation["broken_links"] or validation["duplicate_ids"] or not nav_updated or not opf_updated:
            final_status = "BLOCKED_NEEDS_FIX"

        write_epub(workdir, OUTPUT_EPUB)

    report = {
        "input_epub": str(REQUESTED_INPUT.relative_to(ROOT)),
        "input_epub_exists": REQUESTED_INPUT.exists(),
        "input_epub_used": str(input_epub.relative_to(ROOT)),
        "used_fallback_input": used_fallback,
        "output_epub": str(OUTPUT_EPUB.relative_to(ROOT)),
        "glossary_entries_created": len(TERMS),
        "body_references_inserted": len(events),
        "backlinks_created": backlinks_created,
        "terms_linked": terms_linked,
        "terms_not_found_in_main_text": terms_not_found,
        "terms_skipped": [],
        "reference_events": events,
        "duplicate_ids": validation["duplicate_ids"],
        "broken_links": validation["broken_links"],
        "scan_counts": counts,
        "nav_updated": nav_updated,
        "ncx_updated": ncx_updated,
        "opf_updated": opf_updated,
        "glossary_after_book_10_in_spine": opf_updated,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "final_status": final_status,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("GLOSSARY LINKING COMPLETE")
    print()
    print("Generated:")
    print(str(OUTPUT_EPUB.relative_to(ROOT)))
    print(str(REPORT_PATH.relative_to(ROOT)))
    print()
    print("Status:")
    print(final_status)
    if final_status != "READY_FOR_KINDLE_PREVIEWER":
        print()
        print("Issues:")
        for issue in validation["broken_links"] + validation["duplicate_ids"]:
            print(f"- {issue}")


if __name__ == "__main__":
    main()
