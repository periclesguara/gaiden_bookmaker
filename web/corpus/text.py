import re

NORMALIZER_VERSION = "body-boundaries-v1"
CHUNK_STRATEGY_VERSION = "section-3200-4800-v1"
START = re.compile(r"\*\*\*\s*START OF (?:THIS|THE) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)
END = re.compile(r"\*\*\*\s*END OF (?:THIS|THE) PROJECT GUTENBERG EBOOK.*?\*\*\*"
                 r"|^End of (?:the )?Project Gutenberg(?:'s| EBook).*?$", re.I | re.S | re.M)


def body_only(raw, provenance):
    text = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if "\x00" in text:
        raise ValueError("Binary source")
    original = len(text)
    start, end = START.search(text), END.search(text)
    rules = []
    if end:
        text = text[:end.start()]
        rules.append("gutenberg-footer")
    if start:
        text = text[start.end():]
        rules.append("gutenberg-header")
    heading = provenance.get("body_start_heading")
    if not heading:
        raise ValueError("Body boundary requires explicit source review")
    lines = text.splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if line.strip() == heading]
    if not positions:
        raise ValueError("Reviewed body start heading is absent")
    text = "".join(lines[positions[0]:])
    rules.append("reviewed-frontmatter-boundary")
    cutoff = provenance.get("body_end_before")
    if cutoff:
        matches = list(re.finditer(r"(?m)^[ \t]*" + re.escape(cutoff) + r"[ \t]*$", text))
        if len(matches) != 1:
            raise ValueError("Reviewed body end boundary is ambiguous")
        text = text[:matches[0].start()]
        rules.append("reviewed-distribution-material-boundary")
    text = text.strip() + "\n"
    if len(text) < 500 or "project gutenberg" in text.casefold():
        raise ValueError("Body too short or Gutenberg boilerplate remains")
    sections = provenance.get("section_titles") or []
    for title in sections:
        if not any(line.strip() == title for line in text.splitlines()):
            raise ValueError("Reviewed narrative section is missing")
    return text, {"rules": rules, "original_chars": original, "normalized_chars": len(text),
                  "removed_chars": original - len(text), "section_titles": sections,
                  "section_count": len(sections), "first_body_heading": heading,
                  "body_ending_chars": len(text[-500:]), "coverage": "body slice preserved without rewriting"}


def section_spans(text, titles):
    # Each reviewed story starts its own chunk stream, including split stories.
    boundaries = []
    offset = 0
    previous = None
    for line in text.splitlines(keepends=True):
        label = line.strip()
        if label in titles and label != previous:
            boundaries.append((offset, label))
            previous = label
        elif label and label not in titles:
            previous = None
        offset += len(line)
    if not boundaries:
        return [("Body", text)]
    if boundaries[0][0]:
        boundaries.insert(0, (0, "Preface"))
    return [(label, text[start:boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)])
            for i, (start, label) in enumerate(boundaries)]
