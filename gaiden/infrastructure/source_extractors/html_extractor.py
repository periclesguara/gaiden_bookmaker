from __future__ import annotations

import re
from html import unescape
from pathlib import Path

from .base import canonical_paths, ensure_canonical_dirs, normalize_result, write_source_meta

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None


BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "figcaption",
    "figure",
    "footer",
    "header",
    "hgroup",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}


def html_to_text(raw_html: str) -> str:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        for tag in soup.find_all("br"):
            tag.replace_with("\n")
        for level in range(1, 7):
            for tag in soup.find_all(f"h{level}"):
                tag.insert(0, f"{'#' * level} ")
        for tag in soup.find_all([*BLOCK_TAGS, *(f"h{level}" for level in range(1, 7))]):
            tag.insert_before("\n")
            tag.insert_after("\n")
        text = soup.get_text("")
    else:
        text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", raw_html)
        text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", text)
        text = re.sub(r"(?s)<!--.*?-->", "", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(
            r"(?is)<h([1-6])\b[^>]*>(.*?)</h\1>",
            lambda match: f"\n{'#' * int(match.group(1))} {match.group(2)}\n",
            text,
        )
        text = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|blockquote|tr|td|th|ul|ol)>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", "", text)
        text = unescape(text)

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


class HtmlExtractor:
    input_format = "html"

    def extract(self, original_file: Path, *, book_code: str, lang: str) -> dict:
        paths = canonical_paths(book_code, lang, original_file.suffix.lower() or ".html")
        ensure_canonical_dirs(paths)

        raw_html = original_file.read_text(encoding="utf-8", errors="replace")
        paths.canonical_html.write_text(raw_html, encoding="utf-8")
        paths.canonical_txt.write_text(html_to_text(raw_html) + "\n", encoding="utf-8")

        result = normalize_result(input_format=self.input_format, paths=paths)
        write_source_meta(result, paths)
        return result
