"""Read approved frontmatter from an EPUB spine without importing its cover."""
import re
import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree


_CONTAINER = "META-INF/container.xml"
_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_PAGEBREAK = "::: pagebreak\n:::"
_SKIP_DOCUMENTS = {"cover.xhtml"}


def _member_path(base, href):
    path = PurePosixPath(base).parent / href
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("EPUB com caminho inseguro no manifesto.")
    return str(path)


def _read(archive, member):
    info = archive.getinfo(member)
    if info.file_size > _MAX_DOCUMENT_BYTES:
        raise ValueError("Documento de frontmatter excede o limite de segurança.")
    return archive.read(info)


def _text(element):
    return " ".join("".join(element.itertext()).split())


def _document_markdown(data, name):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
    root = etree.fromstring(data, parser=parser)
    nodes = root.xpath("//*[local-name()='body']//*[local-name()='h1' or local-name()='h2' or local-name()='p' or local-name()='li']")
    rendered = []
    has_heading = False
    for node in nodes:
        value = _text(node)
        if not value:
            continue
        tag = etree.QName(node).localname
        if tag in {"h1", "h2"}:
            rendered.append(f"# {value}")
            has_heading = True
        else:
            rendered.append(value)
    if not rendered:
        return ""
    if not has_heading:
        title_nodes = root.xpath("//*[local-name()='head']/*[local-name()='title']")
        title = _text(title_nodes[0]) if title_nodes else Path(name).stem.replace("_", " ").title()
        rendered.insert(0, f"# {title}")
    return "\n\n".join(rendered)


def approved_frontmatter_markdown(epub_path):
    """Return frontmatter documents in canonical EPUB spine order."""
    with zipfile.ZipFile(epub_path) as archive:
        container = etree.fromstring(_read(archive, _CONTAINER), parser=etree.XMLParser(resolve_entities=False, no_network=True))
        roots = container.xpath("//*[local-name()='rootfile']/@full-path")
        if len(roots) != 1:
            raise ValueError("EPUB sem pacote canônico único.")
        opf_path = roots[0]
        package = etree.fromstring(_read(archive, opf_path), parser=etree.XMLParser(resolve_entities=False, no_network=True))
        manifest = {
            item.get("id"): _member_path(opf_path, item.get("href", ""))
            for item in package.xpath("//*[local-name()='manifest']/*[local-name()='item']")
            if item.get("id") and item.get("href") and item.get("media-type") == "application/xhtml+xml"
        }
        documents = []
        for itemref in package.xpath("//*[local-name()='spine']/*[local-name()='itemref']"):
            member = manifest.get(itemref.get("idref"))
            if not member:
                continue
            basename = PurePosixPath(member).name
            if re.fullmatch(r"chapter_\d+\.xhtml", basename, flags=re.IGNORECASE):
                break
            if basename in _SKIP_DOCUMENTS:
                continue
            rendered = _document_markdown(_read(archive, member), basename)
            if rendered:
                documents.append(rendered)
    return (f"\n\n{_PAGEBREAK}\n\n").join(documents)


def merge_frontmatter_with_body(epub_path, body):
    """Replace the body's minimal title stub with approved EPUB frontmatter."""
    frontmatter = approved_frontmatter_markdown(epub_path)
    body_without_stub = re.sub(r"\A.*?^\\newpage\s*$", "", body, count=1, flags=re.DOTALL | re.MULTILINE).lstrip()
    return f"{frontmatter}\n\n{_PAGEBREAK}\n\n{body_without_stub}" if frontmatter else body
